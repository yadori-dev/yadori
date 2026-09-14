"""道具の共通選択と各入口の配線。差し替えるのはプロセス・端末・Discord の I/O だけ。"""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import final
from unittest.mock import patch

import pytest

from tests.sora import fixed
from tests.test_st_066_discord import _direct, _Gateway  # pyright: ignore[reportPrivateUsage]
from tests.test_st_079_setting import TerminalInput, edit, legacy
from yadori.adapter.dream import ClaudeCodeSummarizing
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.evaluation import ClaudeCodeJudge
from yadori.adapter.place import DiscordPlace, Terminal
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ToolCallFailed
from yadori.domain.evaluation import Asking
from yadori.infrastructure.dream import Dreamer
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.settings import Configuration, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.infrastructure.tools import SelectedCall, ToolChoice

HELP = Path(__file__).parent / "fixtures/tool-help-079.json"


@final
class Processes:
    def __init__(self, reply: str = "YADORI79_OK\n【気持ち】0.4 うれしい") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, Path]] = []
        self.fail = False

    def run(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs, env
        name = argv[0]
        if "--help" in argv:
            helps: dict[str, str] = json.loads(HELP.read_text())  # pyright: ignore[reportAny]
            return subprocess.CompletedProcess(argv, 0, helps[name], "")
        if "login" in argv:
            return subprocess.CompletedProcess(argv, 0, "Logged in using ChatGPT", "")
        if input is None:
            raise AssertionError(argv)
        self.calls.append((name, input, Path(cwd or ".")))
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "failure")
        if name == "codex":
            target = Path(argv[argv.index("--output-last-message") + 1])
            _ = target.write_text(self.reply)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": self.reply}), "")


def test_ST_079_003_IT_079_002_設定変更がchatとDiscordと要約と判定へ届く(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YADORI_HOME", str(tmp_path))
    legacy(tmp_path)
    processes = Processes()
    with (
        patch("subprocess.run", processes.run),
        patch("shutil.which", return_value="/tool"),
    ):
        for chosen, order in (("codex", "codex claude agy"), ("claude", "claude codex agy")):
            assert edit(tmp_path, f"4\n{order}\ny\n5\n")[0] == 0
            assert ToolChoice().select("対話起動") == chosen
            assert (
                Startup(default=fixed(CharacterPairs())).run(
                    lambda turn, settings: Terminal(
                        turn, settings.dweller, io.StringIO("こんにちは\n\n"), io.StringIO()
                    )
                )
                == 0
            )
            gateway = _Gateway([_direct("園芸の話をしたいです")])
            assert (
                Startup(default=fixed(CharacterPairs())).run(
                    lambda turn, settings, gateway=gateway: DiscordPlace(
                        turn, settings.dweller, 111, gateway
                    )
                )
                == 0
            )
            assert gateway.sent and "YADORI79_OK" in gateway.sent[0][0]
            settings = SettingsFile().read()
            memories = SqliteMemories(settings.memories_path)
            try:
                count = memories.count_episodes(settings.dweller.id)
                identity = memories.current_identity(settings.dweller.id)
                assert identity is not None
                processes.reply = "- 園芸の話をした\n気づき: 無し"
                summarized = ClaudeCodeSummarizing(SelectedCall("夢", 10)).summarize(
                    identity, memories.recent(settings.dweller.id, 1)
                )
                assert summarized.gists == ("園芸の話をした",)
                processes.reply = '[{"q": 1, "same": [1]}]'
                judge = ClaudeCodeJudge(SelectedCall("下書き", 10))
                assert len(judge.pairs([Asking("トマトの話", ("トマトを植えた",))])) == 1
                assert chosen in judge.name or (chosen == "claude" and judge.name == "opus")
                assert memories.count_episodes(settings.dweller.id) == count
                assert [name for name, _, _ in processes.calls[-4:]] == [chosen] * 4
                assert all(not cwd.exists() for _, _, cwd in processes.calls)
                processes.reply = "YADORI79_OK\n【気持ち】0.4 うれしい"
            finally:
                memories.close()


def test_IT_079_002_処理開始後の設定変更と実行失敗では送り先を変えない(tmp_path: Path) -> None:
    legacy(tmp_path)
    pending = SelectedCall("夢", 10, tmp_path)
    assert edit(tmp_path, "4\nclaude codex\ny\n5\n")[0] == 0
    processes = Processes()
    with patch("subprocess.run", processes.run), patch("shutil.which", return_value="/tool"):
        assert pending.ask("前置き", "発話")
        processes.fail = True
        with pytest.raises(ToolCallFailed):
            _ = pending.ask("前置き", "次の発話")
    assert [name for name, _, _ in processes.calls] == ["codex", "codex"]


@pytest.mark.parametrize("value", [[], ["unknown"], ["codex", "codex"], "codex"])
def test_ST_079_003_不正な順序は黙って無視しない(tmp_path: Path, value: object) -> None:
    legacy(tmp_path)
    config = Configuration(tmp_path).load()
    config.data["priority"] = value
    with pytest.raises(Exception, match="優先順"):
        config.save()


def test_ST_079_003_未導入は飛ばし明示指定は設定を書き換えない(tmp_path: Path) -> None:
    legacy(tmp_path)
    choice = ToolChoice(tmp_path)

    def found(name: str) -> str | None:
        return "/claude" if name == "claude" else None

    with patch("shutil.which", side_effect=found):
        assert choice.select("対話") == "claude"
        with pytest.raises(ToolCallFailed):
            _ = choice.select("対話", explicit="codex")
    assert Configuration(tmp_path).load().order()[0] == "codex"
    with patch("shutil.which", return_value=None):
        with pytest.raises(ToolCallFailed, match="導入"):
            _ = choice.select("対話")


def test_ST_079_003_記憶が増えていない夢は道具を要求しない(tmp_path: Path) -> None:
    legacy(tmp_path)
    settings = SettingsFile(tmp_path).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(tmp_path).settle(memories, settings)
    memories.close()
    with (
        patch("shutil.which", return_value=None),
        patch("subprocess.run", side_effect=AssertionError("呼出禁止")),
    ):
        assert Dreamer(tmp_path, default=fixed(CharacterPairs())).run() == 0


def test_ST_079_001_直接のsettingでは保存後も道具を起こさない(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YADORI_HOME", str(tmp_path))
    captured: list[str] = []

    def launch(argv: Sequence[str], **kwargs: object) -> int:
        del kwargs
        captured.append(argv[0])
        assert SettingsFile().read().dweller.name == "空"
        # フックが参照する値は、このプロセスの起動時の写しに固定されている。
        assert "YADORI_SETTINGS_SNAPSHOT" in os.environ
        return 0

    # 認証・設定準備を含む既存の専用起動は契約テストで検査する。
    # ここでは設定だけの直接入口が外の道具を呼ばないことを観測する。
    with (
        patch("subprocess.call", launch),
        patch("sys.stdin", TerminalInput("空\nそら\n持ち主\n名乗り\n.\n\ny\n5\n")),
        patch("sys.stdout", TerminalInput()),
    ):
        assert Entry(["yadori", "setting"]).run() == 0
    assert captured == []


def test_ST_079_003_名乗りなしの下書きと追記が共通順を使う(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.appending import HOW, first_records
    from yadori.infrastructure.draft import Drafter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YADORI_HOME", str(home))
    # 人物設定がなく、道具の共通設定だけがある場合も判定できる。
    _ = (home / "settings.toml").write_text('version=1\npriority=["claude", "codex"]\n')
    records = tmp_path / "records"
    _ = first_records(records)
    processes = Processes('[{"q": 1, "same": [1]}]')
    out = tmp_path / "draft.toml"
    with patch("subprocess.run", processes.run), patch("shutil.which", return_value="/tool"):
        assert Drafter([records], out, default=fixed(CharacterPairs()), how=HOW).run() == 0
    assert processes.calls and {name for name, _, _ in processes.calls} == {"claude"}
    before = len(processes.calls)
    with (
        patch("subprocess.run", side_effect=AssertionError("判定不要")),
        patch("shutil.which", return_value=None),
    ):
        assert (
            Drafter([records], out, append=True, default=fixed(CharacterPairs()), how=HOW).run()
            == 0
        )
    assert len(processes.calls) == before


def test_ST_079_003_夢の入口も変更した人物の名乗りと共通順を使う(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from yadori.domain.memory import Moved
    from yadori.usecase.conversation import Conversation

    monkeypatch.setenv("YADORI_HOME", str(tmp_path))
    legacy(tmp_path)
    assert edit(tmp_path, "4\nclaude codex\ny\n5\n")[0] == 0
    settings = SettingsFile().read()
    memories = SqliteMemories(settings.memories_path)
    Startup().settle(memories, settings)
    _ = Conversation(memories, CharacterPairs(), lambda: datetime.now(UTC)).remember(
        settings.dweller.id, "トマトの苗が育ってうれしい", "よかったですね", Moved(0.9, "うれしい")
    )
    memories.close()
    assert edit(tmp_path, "2\n私は空です。新しい話し方です。\n.\ny\n5\n")[0] == 0
    processes = Processes("- トマトの苗が育った\n気づき: 無し")
    with patch("subprocess.run", processes.run), patch("shutil.which", return_value="/tool"):
        assert Dreamer(default=fixed(CharacterPairs())).run() == 0
    assert [name for name, _, _ in processes.calls] == ["claude"]
    memories = SqliteMemories(settings.memories_path)
    try:
        assert memories.count_episodes(settings.dweller.id) == 1
        identity = memories.current_identity(settings.dweller.id)
        assert identity is not None and identity.text == "私は空です。新しい話し方です。"
    finally:
        memories.close()


def test_IT_079_002_用途に未対応の候補だけを事前に飛ばす(tmp_path: Path) -> None:
    legacy(tmp_path)
    assert edit(tmp_path, "4\nagy claude codex\ny\n5\n")[0] == 0
    processes = Processes()

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv == ["agy", "changelog"]:
            return subprocess.CompletedProcess(argv, 0, "1.2.2:\n", "")
        return processes.run(argv, **kwargs)  # pyright: ignore[reportArgumentType]

    with patch("shutil.which", return_value="/tool"), patch("subprocess.run", run):
        assert ToolChoice(tmp_path).select("応対", internal=True) == "claude"
    assert processes.calls == []

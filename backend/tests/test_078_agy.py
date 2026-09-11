"""agy の実物から採った通知で、記憶の入口と回復を確かめる。"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import final

import pytest

from tests.sora import fixed
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool.agy_notice import AgyJson, AgyNotice
from yadori.adapter.tool.agy_words import AgyWords
from yadori.infrastructure.agy import AgyHook, AgyMemory
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup


@final
class Probe:
    def __init__(self, path: Path) -> None:
        self.home = path / "dweller"
        self.home.mkdir()
        _ = (self.home / "dweller.toml").write_text(
            'id="sora"\nname="そら"\nnickname="そら"\nowner="架空の持ち主"\n'
        )
        _ = (self.home / "identity.md").write_text("わたしはそらです。ていねいに話します。")
        self.run = self.home / "agy/sessions/check"
        (self.run / "turns").mkdir(parents=True)
        _ = (self.run / "session.lock").touch()
        self.fixture = AgyJson.object(
            (Path(__file__).parent / "fixtures/agy-1.2.1.json").read_text()
        )
        self.pre = AgyJson.object(json.dumps(self.fixture["pre"]))
        self.stop = AgyJson.object(json.dumps(self.fixture["stop"]))
        self.session = AgyJson.text(self.pre, "conversationId")
        rows = self.fixture["transcript"]
        assert isinstance(rows, list)
        self.rows: list[dict[str, object]] = [
            AgyJson.object(json.dumps(row))
            for row in rows  # pyright: ignore[reportUnknownVariableType]
        ]
        self.transcript = (
            self.run
            / "gemini/antigravity-cli/brain"
            / self.session
            / ".system_generated/logs/transcript_full.jsonl"
        )
        self.transcript.parent.mkdir(parents=True)
        self.startup = Startup(self.home, default=fixed(CharacterPairs()))
        self.memory = AgyMemory(self.home, self.startup)
        _ = self.memory.prepare()

    def write(self, rows: list[dict[str, object]]) -> None:
        _ = self.transcript.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        )

    def call(self, event: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
        output = io.StringIO()
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps(self.pre if event == "pre" else self.stop))
        )
        monkeypatch.setattr(sys, "stdout", output)
        code = AgyHook(event, self.run, self.home, self.startup).run()
        return code, output.getvalue()

    def count(self) -> int:
        settings = SettingsFile(self.home).read()
        store = SqliteMemories(settings.memories_path)
        try:
            return len(store.recent(settings.dweller.id, 100))
        finally:
            store.close()


def test_ST_078_002_IT_078_002_同じ通知は一往復だけ残し同文の別発話を区別する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    code, context = probe.call("pre", monkeypatch)
    assert code == 0 and "そら" in context and "気持ち" in context
    assert probe.call("pre", monkeypatch) == (code, context)
    probe.write(probe.rows)
    assert probe.call("stop", monkeypatch)[0] == 0
    assert probe.call("stop", monkeypatch)[0] == 0
    assert probe.count() == 1
    rows = [dict(row, step_index=AgyJson.number(row, "step_index") + 10) for row in probe.rows]
    probe.write([*probe.rows, rows[0]])
    assert probe.call("pre", monkeypatch)[0] == 0
    probe.write([*probe.rows, *rows])
    assert probe.call("stop", monkeypatch)[0] == 0
    assert probe.count() == 2


def test_ST_078_003_IT_078_003_保存の直後に中断しても原文を再送して一度だけ保存する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    probe.write(probe.rows)
    notice = AgyNotice.read("stop", json.dumps(probe.stop), probe.run)
    record_path = probe.run / "turns/0.json"
    record = AgyJson.object(record_path.read_text())
    record["reply"] = notice.reply
    AgyJson.write(record_path, record)
    probe.memory.replay(record_path)
    record["saved"] = False
    AgyJson.write(record_path, record)
    probe.memory.recover(probe.home / "agy/sessions")
    assert not probe.run.exists()
    assert probe.count() == 1


@pytest.mark.parametrize(
    "failure", ["different-reply", "different-user", "foreign", "broken", "unknown-stop"]
)
def test_ST_078_002_ST_078_003_IT_078_002_対応が崩れた通知を確定しない(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    probe.write(probe.rows)
    assert probe.call("stop", monkeypatch)[0] == 0
    if failure == "different-reply":
        probe.rows[-1]["content"] = "違う返事です。"
    elif failure == "different-user":
        probe.rows[0]["content"] = (
            "<USER_REQUEST>\n違う発話\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n時刻"
        )
    elif failure == "foreign":
        probe.stop["conversationId"] = "00000000-0000-0000-0000-000000000000"
    elif failure == "broken":
        _ = (probe.run / "turns/0.json").write_text("broken")
    else:
        probe.stop["terminationReason"] = "NEW_UNSUPPORTED_REASON"
    probe.write(probe.rows)
    assert probe.call("stop", monkeypatch)[0] == 2
    assert probe.count() == 1


def test_IT_078_003_壊れた回復記録は消さず次の起動を断る(tmp_path: Path) -> None:
    probe = Probe(tmp_path)
    record = probe.run / "turns/0.json"
    _ = record.write_text("broken")
    with pytest.raises(ValueError):
        probe.memory.recover(probe.home / "agy/sessions")
    assert record.read_text() == "broken"


def test_ST_078_002_中断した返事を記憶しない(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    probe.stop.update({"fullyIdle": False, "error": "interrupted"})
    assert probe.call("stop", monkeypatch)[0] == 0
    assert probe.count() == 0


def test_IT_078_002_気持ちの読み取りを既存と共用する() -> None:
    assert AgyWords().parted("そらです。\n【気持ち】+0.2 確認できた").moved.delta == 0.2


def test_ST_078_003_IT_078_003_保存先の失敗を回復して原文を一度だけ残す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    probe.write(probe.rows)
    with sqlite3.connect(probe.home / "memories.sqlite") as blocked:
        _ = blocked.execute("BEGIN EXCLUSIVE")
        assert probe.call("stop", monkeypatch)[0] == 2
        blocked.rollback()
    record = AgyJson.object((probe.run / "turns/0.json").read_text())
    assert record["reply"] == probe.rows[-1]["content"] and record["saved"] is False
    probe.memory.recover(probe.home / "agy/sessions")
    assert probe.count() == 1


def test_IT_078_003_未確認の返事は異常終了後に捨てない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    with pytest.raises(ValueError, match="完成状態"):
        probe.memory.recover(probe.home / "agy/sessions")
    assert (probe.run / "turns/0.json").exists()


def test_ST_078_004_IT_078_001_実際の起動で設定と履歴を普段の領域から分ける(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import shutil

    from yadori.adapter.tool.agy_session import AgySession

    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap が必要")
    usual = tmp_path / "usual"
    data = usual / ".gemini/antigravity-cli"
    (data / "cache").mkdir(parents=True)
    work = tmp_path / "workspace"
    work.mkdir()
    _ = (data / "settings.json").write_text(
        json.dumps(
            {
                "trustedWorkspaces": [str(work)],
                "toolPermission": "strict",
                "colorScheme": "dark",
                "enableTerminalSandbox": True,
            }
        )
    )
    _ = (data / "cache/onboarding.json").write_text(
        json.dumps(
            {
                "onboardingComplete": True,
                "consumerOnboardingComplete": True,
            }
        )
    )
    _ = (data / "usual-history").write_text("usual")
    _ = (work / "GEMINI.md").write_text("foreign-persona")
    (work / ".agents").mkdir()
    _ = (work / ".agents/hooks.json").write_text("foreign-hook")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "agy"
    _ = executable.write_text(
        f"#!{sys.executable}\n"
        + "import sys,json,pathlib\n"
        + "if sys.argv[1:] == ['changelog']:\n print('1.2.1:');sys.exit(0)\n"
        + f"data=pathlib.Path({str(data)!r})\n"
        + "result=json.loads((data/'settings.json').read_text())\n"
        + "result['usual_history']=(data/'usual-history').exists()\n"
        + "result['persona']=pathlib.Path('GEMINI.md').read_text()\n"
        + "result['foreign_hook']=pathlib.Path('.agents/hooks.json').exists()\n"
        + "(data/'session-history').write_text('synthetic')\n"
        + "pathlib.Path('observed.json').write_text(json.dumps(result))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: usual))
    environment = dict(os.environ)
    environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
    session = AgySession(tmp_path / "dweller", work, environment)
    assert session.launch() == 0
    observed = AgyJson.object((work / "observed.json").read_text())
    assert observed["persona"] == "" and observed["foreign_hook"] is False
    assert observed["usual_history"] is False
    assert observed["toolPermission"] == "strict" and observed["enableTerminalSandbox"] is True
    assert (data / "usual-history").read_text() == "usual"
    assert not (data / "session-history").exists()
    assert not list((tmp_path / "dweller/agy/sessions").iterdir())
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "CASCADE_GLOBAL_CONFIG_OVERRIDE",
    ):
        changed = dict(environment, **{key: "synthetic"})
        with pytest.raises(ValueError, match="認証"):
            _ = AgySession(tmp_path / "dweller", work, changed).prepare()


@pytest.mark.parametrize(
    "name,persistent,outside,decision",
    [
        ("view_file", False, False, "allow"),
        ("view_file", False, True, "ask"),
        ("write_to_file", False, False, "ask"),
        ("run_command", False, False, "ask"),
        ("run_command", True, False, "deny"),
        ("invoke_subagent", False, False, "deny"),
        ("schedule", False, False, "deny"),
    ],
)
def test_ST_078_004_道具を呼ぶ前に背景処理と下位担当を拒否する(
    name: str,
    persistent: bool,
    outside: bool,
    decision: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    fixture_key = "tool_read" if name == "view_file" else "tool_persistent"
    payload = AgyJson.object(json.dumps(probe.fixture[fixture_key]))
    payload["conversationId"] = probe.session
    call = AgyJson.object(json.dumps(payload["toolCall"]))
    arguments = AgyJson.object(json.dumps(call["args"]))
    call["name"] = name
    arguments["RunPersistent"] = persistent
    if outside:
        arguments["AbsolutePath"] = str(tmp_path / "outside.txt")
    call["args"] = arguments
    payload["toolCall"] = call
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert AgyHook("tool", probe.run, probe.home, probe.startup).run() == 0
    result = AgyJson.object(output.getvalue())
    assert result.get("decision") == decision
    assert probe.count() == 0

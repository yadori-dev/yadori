"""道具の実物・固定観測・端末の失敗境界を対応させる。"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import final

import pytest

from tests.sora import fixed
from tests.test_st_057_dream import _home  # pyright: ignore[reportPrivateUsage]
from tests.test_st_080_clarification import AT, FIXTURES, PAIRS, ObservedCall
from yadori.adapter.dream.clarification import ToolExplaining
from yadori.adapter.store import SqliteMemories
from yadori.domain.dream import CannotDream, Summarized
from yadori.domain.memory import Episode, Identity
from yadori.infrastructure.dream import Dreamer
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.infrastructure.tools import SelectedCall
from yadori.usecase.conversation import Conversation


@final
class Summary:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing

    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        del identity, episodes
        if self.failing:
            raise CannotDream("通常夢の失敗")
        return Summarized(("架空の相談",), None)


def seeded(home: Path, *, old: bool) -> tuple[int, int]:
    _ = _home(home)
    settings = SettingsFile(home).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(home).settle(memories, settings)
    conversation = Conversation(memories, PAIRS, lambda: AT)
    first = conversation.remember(
        "sora",
        "資料の整理について相談するので提案してもらえますか",
        "資料は見出しごとに分けて整理しますか",
        source="proposal",
        session_id="session",
    )
    target = conversation.remember(
        "sora",
        "その形でお願い",
        "始めます",
        source="answer",
        session_id="session",
        previous_source="proposal",
    )
    if old:
        _ = memories.record_dream("sora", AT, AT, AT, 2, 0, None)
    else:
        memories.record_retrieval((target.id,), AT)
    memories.close()
    return first.id, target.id


@pytest.mark.parametrize("old,failing,code", [(True, False, 0), (False, True, 1)])
def test_ST_080_006_IT_080_005_通常夢が空か失敗でも補完の件数と根拠が残る(
    tmp_path: Path,
    old: bool,
    failing: bool,
    code: int,
) -> None:
    home = tmp_path / "home"
    _, target = seeded(home, old=old)
    writing = io.StringIO()
    actual = Dreamer(
        home,
        Summary(failing),
        fixed(PAIRS),
        writing=writing,
        explaining=ToolExplaining(ObservedCall()),
    ).run()
    assert actual == code
    output = writing.getvalue()
    assert "返答の補完: 1 件" in output and "失敗: 0 件" in output
    assert "その形でお願い" in output and "資料は見出しごと" in output
    if old:
        assert "新しい記憶がありません" in output
    memories = SqliteMemories(home / "memories.sqlite")
    assert memories.clarification(target, 1) is not None
    memories.close()


@pytest.mark.parametrize(
    "name",
    [
        "headings",
        "backup",
        "deny",
        "continue",
        "choice",
        "ambiguous",
        "standalone",
        "conditional",
        "uncertain",
        "independent",
        "two_targets",
        "future",
    ],
)
def test_ST_080_002_IT_080_003_実物の観測値から応答種別と制限を読む(name: str) -> None:
    cases: list[dict[str, object]] = json.loads((FIXTURES / "cases.json").read_text())["cases"]  # pyright: ignore[reportAny]
    case = next(one for one in cases if one["name"] == name)
    target = Episode(2, str(case["utterance"]), "完了したという未来の返事", 1, AT)
    previous = Episode(1, "相談です", str(case["proposal"]), 1, AT)
    record = ToolExplaining(ObservedCall(name)).explain(target, (previous,), AT)
    assert (record.kind if record.status == "clarified" else record.status) == case["expected"]
    if name == "conditional":
        assert record.text and "公開" in record.text and "別途確認" in record.text
    if name == "future":
        assert record.text and "今回だけ" in record.text


@pytest.mark.contract
def test_IT_080_003_実物でも固定した補完の契約を満たす() -> None:
    target = Episode(2, "その形でお願い", "未来の結果", 1, AT)
    previous = Episode(1, "相談です", "資料は見出しごとに分けて整理しますか", 1, AT)
    actual = ToolExplaining(SelectedCall("補完の契約確認", 120)).explain(target, (previous,), AT)
    assert actual.status == "clarified" and actual.kind == "approval" and actual.sources == (1,)
    assert actual.text and "見出し" in actual.text


@final
class BadCall:
    def __init__(self, text: str) -> None:
        self.text = text

    def ask(self, preface: str, spoken: str) -> str:
        del preface, spoken
        return self.text


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"status":"clarified"}',
        '{"status":"unknown","kind":null,"text":null,"reason":"x","sources":[]}',
    ],
)
def test_IT_080_003_不正な返り値を断る(body: str) -> None:
    with pytest.raises(CannotDream):
        _ = ToolExplaining(BadCall(body)).explain(Episode(2, "はい", "", 1, AT), (), AT)


def test_ST_080_006_IT_080_005_途中失敗も確定件数と未処理を表示する(tmp_path: Path) -> None:
    import sqlite3

    home = tmp_path / "home"
    _, target = seeded(home, old=True)
    # 所属不明の候補を先に処理済みにするのではなく、同じバッチの後半で障害を起こす。
    store = SqliteMemories(home / "memories.sqlite")
    following = store.write_episode("sora", "いいよ", "", 1, AT)
    store.close()
    with sqlite3.connect(home / "memories.sqlite") as connection:
        _ = connection.execute(
            "CREATE TRIGGER failing BEFORE INSERT ON clarification WHEN NEW.episode_id = "
            + str(following.id)
            + " BEGIN SELECT RAISE(ABORT, 'disk'); END"
        )
    writing = io.StringIO()
    actual = Dreamer(
        home, Summary(), fixed(PAIRS), writing=writing, explaining=ToolExplaining(ObservedCall())
    ).run()
    assert actual == 1
    assert "返答の補完: 1 件" in writing.getvalue()
    assert "失敗: 1 件、未処理: あり" in writing.getvalue()
    store = SqliteMemories(home / "memories.sqlite")
    assert store.clarification(target, 1) is not None
    assert store.clarification(following.id, 1) is None
    store.close()


def test_ST_080_008_IT_080_005_補完も共通候補だけ使い送信後失敗で再送しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    from tests.test_it_079_tools import Processes
    from tests.test_st_079_setting import legacy
    from yadori.adapter.tool import ToolCallFailed

    monkeypatch.setenv("YADORI_HOME", str(tmp_path))
    legacy(tmp_path)
    processes = Processes()
    processes.fail = True
    with patch("subprocess.run", processes.run), patch("shutil.which", return_value="/tool"):
        call = SelectedCall("夢の補完", 120, tmp_path)
        with pytest.raises(ToolCallFailed):
            _ = call.ask("架空の資料", "はい")
    assert len(processes.calls) == 1


@pytest.mark.parametrize("provider", ["claude", "codex", "agy"])
def test_ST_080_004_IT_080_004_根拠の条件が収まらないとき補完全体を省く(provider: str) -> None:
    from yadori.adapter.tool import ClaudeWords, CodexWords
    from yadori.adapter.tool.agy_words import AgyWords
    from yadori.domain.memory import (
        Clarification,
        Found,
        Identity,
        Recollection,
        Retrieval,
        State,
    )

    words = {"claude": ClaudeWords(), "codex": CodexWords(), "agy": AgyWords()}[provider]
    target = Episode(2, "お願い", "下書きを作ります", 1, AT)
    root = Episode(1, "相談", "あ" * 6000 + "公開には別途確認が必要", 1, AT)
    explanation = Clarification(2, 1, "clarified", "approval", "下書き作成を承認", "根拠", AT, (1,))
    found = Found(target, 0.95, Retrieval(0, None), "test", explanation, (root,))
    recalled = Recollection(
        Identity(1, "そらです"), (), (found,), State.from_shifts((), AT), None, AT
    )
    result = words.hook_response(recalled)
    assert len(result.encode()) <= 9000
    assert "下書き作成を承認" not in result
    assert "省略" in result


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_ST_080_001_IT_080_001_実際の通知入口で連続と中断を保存する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    import sys

    from yadori.infrastructure.claude import ClaudeHook
    from yadori.infrastructure.codex import CodexHook

    home = _home(tmp_path / "home")
    run_dir = home / provider / "sessions" / "run"
    run_dir.mkdir(parents=True)
    _ = (run_dir / "session.lock").write_text("")
    startup = Startup(home, default=fixed(PAIRS))
    identity_key = "prompt_id" if provider == "claude" else "turn_id"
    prompts = "UserPromptSubmit" if provider == "claude" else "user-prompt-submit"
    stops = "Stop" if provider == "claude" else "stop"
    interrupted = "StopFailure" if provider == "claude" else "interrupt"
    for index, utterance in enumerate(
        ("資料の提案をお願いします", "その形でお願い", "途中で止める", "いいよ")
    ):
        payload: dict[str, object] = {
            "session_id": "11111111-1111-4111-8111-111111111111",
            identity_key: f"00000000-0000-4000-8000-{index:012d}",
            "prompt": utterance,
        }
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        hook = (
            ClaudeHook(prompts, run_dir, home, startup)
            if provider == "claude"
            else CodexHook(prompts, run_dir, home, startup)
        )
        assert hook.run() == 0
        payload["last_assistant_message"] = "資料は見出しごとに分けますか\n【気持ち】0 変化なし"
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        event = interrupted if index == 2 else stops
        hook = (
            ClaudeHook(event, run_dir, home, startup)
            if provider == "claude"
            else CodexHook(event, run_dir, home, startup)
        )
        assert hook.run() == 0
    memories = SqliteMemories(home / "memories.sqlite")
    episodes = memories.episodes_after("sora", None)
    assert len(episodes) == 3
    assert episodes[1].session_id == provider + ":11111111-1111-4111-8111-111111111111"
    assert episodes[1].previous_source == episodes[0].source
    assert episodes[2].previous_source is None
    memories.close()

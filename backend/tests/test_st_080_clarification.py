"""原文・根拠・補完・再検索を、保存先と会話の公開口から確認する。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.dream.clarification import ToolExplaining
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.adapter.store.clarification import ClarificationData
from yadori.adapter.tool import ClaudeWords, ToolCallFailed
from yadori.adapter.tool.continuity import Continuity
from yadori.domain.dream import CannotDream
from yadori.domain.memory import (
    Clarification,
    Dweller,
    EmbeddingsUnavailable,
    Episode,
    HowToRecall,
    Memories,
    Provenance,
    RememberingConflict,
    Vector,
)
from yadori.usecase.conversation import Conversation
from yadori.usecase.dream.clarification import Clarifying

AT = datetime(2026, 9, 14, tzinfo=UTC)
DWELLER = Dweller("fiction", "owner", "そら", "そら")
FIXTURES = Path(__file__).parent / "fixtures" / "clarification"
PAIRS = CharacterPairs()


@final
class ObservedCall:
    """実物から採取した返り値を、同じ架空の入力にだけ返す。"""

    def __init__(self, name: str = "headings", filename: str = "observed.json") -> None:
        self.name = name
        self.filename = filename
        self.calls = 0

    def ask(self, preface: str, spoken: str) -> str:
        del preface
        self.calls += 1
        observed: list[dict[str, object]] = json.loads((FIXTURES / self.filename).read_text())  # pyright: ignore[reportAny]
        record = next(one for one in observed if one["name"] == self.name)
        raw = ClarificationData.text(record["raw"])
        # 根拠番号だけを実行ごとに保存先が付けた番号へ移す。説明と判定は観測値そのもの。
        request: dict[str, object] = json.loads(spoken)  # pyright: ignore[reportAny]
        preceding: object = request["preceding"]
        if not isinstance(preceding, list) or not preceding:
            raise AssertionError("観測した入力と違う")
        previous: object = preceding[-1]  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(previous, dict):
            raise AssertionError("観測した入力と違う")
        response: dict[str, object] = json.loads(raw)  # pyright: ignore[reportAny]
        if response["sources"]:
            response["sources"] = [previous["id"]]
        return json.dumps(response, ensure_ascii=False)


@final
class NoCall:
    def explain(
        self, target: Episode, preceding: tuple[Episode, ...], at: datetime
    ) -> Clarification:
        del target, preceding, at
        raise AssertionError("文脈欠損や対象外の長さで生成してはいけない")


@pytest.fixture(params=["memory", "sqlite"])
def memories(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Memories]:
    kind: str = request.param  # pyright: ignore[reportAny]
    store = InMemoryMemories() if kind == "memory" else SqliteMemories(tmp_path / "db")
    store.settle(DWELLER)
    _ = store.write_identity(DWELLER.id, "わたしはそらです")
    yield store
    if isinstance(store, SqliteMemories):
        store.close()


def pair(memories: Memories, utterance: str = "その形でお願い") -> tuple[Episode, Episode]:
    conversation = Conversation(memories, PAIRS, lambda: AT)
    proposal = conversation.remember(
        DWELLER.id,
        "資料の整理について相談するので提案してもらえますか",
        "資料は見出しごとに分けて整理しますか",
        source="proposal",
        session_id="session",
    )
    answer = conversation.remember(
        DWELLER.id,
        utterance,
        "これから始めます",
        source="answer",
        session_id="session",
        previous_source="proposal",
    )
    return proposal, answer


def test_ST_080_001_IT_080_001_所属と直接の先行出典を原文と保存する(memories: Memories) -> None:
    proposal, answer = pair(memories)
    assert memories.episode(answer.id) == answer
    assert memories.episode_from_source(DWELLER.id, answer.previous_source or "") == proposal
    same = Conversation(memories, PAIRS, lambda: AT).remember(
        DWELLER.id,
        answer.utterance,
        answer.reply,
        source="answer",
        session_id="session",
        previous_source="proposal",
    )
    assert same == answer
    with pytest.raises(RememberingConflict):
        _ = Conversation(memories, PAIRS, lambda: AT).remember(
            DWELLER.id, answer.utterance, answer.reply, source="answer", session_id="different"
        )
    assert memories.count_episodes(DWELLER.id) == 2


def test_ST_080_004_ST_080_005_IT_080_004_古い承認を根拠付きで検索し索引だけ作り直す(
    memories: Memories,
) -> None:
    proposal, target = pair(memories)
    call = ObservedCall()
    clarifying = Clarifying(memories, PAIRS, ToolExplaining(call), lambda: AT)
    result = clarifying.run(DWELLER.id)
    assert len(result.records) == 1 and not result.remaining
    assert memories.episode(target.id) == target
    assert memories.count_episodes(DWELLER.id) == 2
    assert memories.retrieval(target.id).count == 0
    # 最新の夢と直近の記憶に対象を入れない。
    _ = memories.record_dream(DWELLER.id, AT, AT, AT, 0, 0, "別件")
    conversation = Conversation(memories, PAIRS, lambda: AT, HowToRecall(0, 5, 0.05, 0.05))
    query = result.records[0].text or ""
    found = conversation.recall(DWELLER.id, query).found
    matching = [one for one in found if one.episode.id == target.id]
    assert len(matching) == 1 and matching[0].evidence == (proposal,)
    assert matching[0].clarification == result.records[0]
    words = ClaudeWords().preface(conversation.recall(DWELLER.id, query))
    assert query in words and proposal.reply in words and target.utterance in words
    assert "将来の操作許可にはしない" in words
    memories.clear_index(DWELLER.id)
    assert conversation.rebuild_index(DWELLER.id) == 3
    assert any(one.episode.id == target.id for one in conversation.recall(DWELLER.id, query).found)
    assert not clarifying.run(DWELLER.id).records
    assert call.calls == 1


@pytest.mark.parametrize(
    "session,previous", [(None, None), ("session", "missing"), ("other", "proposal")]
)
def test_ST_080_001_ST_080_002_所属不明と先行欠損と別会話を推定で埋めない(
    memories: Memories,
    session: str | None,
    previous: str | None,
) -> None:
    proposal, _ = pair(memories, "この発話は二十文字より長いので補完対象から外れる")
    _ = memories.write_episode(
        DWELLER.id,
        "いいよ",
        "完了しました",
        1,
        AT,
        source="gap",
        session_id=session,
        previous_source=previous,
    )
    result = Clarifying(memories, PAIRS, NoCall(), lambda: AT).run(DWELLER.id)
    assert len(result.records) == 1 and result.records[0].status == "deferred"
    assert result.records[0].sources == ()
    assert memories.retrieval(proposal.id).count == 0


def test_ST_080_002_前後の空白とUnicodeコードポイントの境界(memories: Memories) -> None:
    for length in (20, 21):
        _ = memories.write_episode(DWELLER.id, "\u3000" + "😀" * length + "\t", "返事", 1, AT)
    result = Clarifying(memories, PAIRS, NoCall(), lambda: AT).run(DWELLER.id)
    assert len(result.records) == 1


def test_ST_080_003_古い候補を100件ずつ独立して処理する(memories: Memories) -> None:
    for index in range(101):
        _ = memories.write_episode(DWELLER.id, "はい", "返事", 1, AT + timedelta(seconds=index))
    _ = memories.record_dream(DWELLER.id, AT, AT, AT + timedelta(days=1), 101, 0, None)
    clarifying = Clarifying(memories, PAIRS, NoCall(), lambda: AT)
    assert len(clarifying.run(DWELLER.id).records) == 100
    remaining = clarifying.run(DWELLER.id)
    assert len(remaining.records) == 1 and not remaining.remaining
    assert not clarifying.run(DWELLER.id).records


def test_ST_080_003_IT_080_002_索引の書き込み失敗では説明も確定せず再試行できる(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db"
    store = SqliteMemories(path)
    store.settle(DWELLER)
    _ = store.write_identity(DWELLER.id, "わたしはそらです")
    _, target = pair(store)
    with sqlite3.connect(path) as connection:
        _ = connection.execute(
            "CREATE TRIGGER failing BEFORE INSERT ON clarification_index"
            + " BEGIN SELECT RAISE(ABORT, 'disk'); END"
        )
    clarifying = Clarifying(store, PAIRS, ToolExplaining(ObservedCall()), lambda: AT)
    with pytest.raises(sqlite3.IntegrityError, match="disk"):
        _ = clarifying.run(DWELLER.id)
    assert store.clarification(target.id, 1) is None
    assert store.episode(target.id) == target
    with sqlite3.connect(path) as connection:
        _ = connection.execute("DROP TRIGGER failing")
    assert len(clarifying.run(DWELLER.id).records) == 1
    store.close()
    reopened = SqliteMemories(path)
    assert reopened.episode(target.id) == target
    assert reopened.clarification(target.id, 1) is not None
    reopened.close()


def test_ST_080_001_IT_080_001_通知の中断と未保存を飛び越さない(tmp_path: Path) -> None:
    continuity = Continuity(tmp_path)
    assert continuity.begin("s", "A") is None
    continuity.finish("s", "A")
    assert Continuity(tmp_path).begin("s", "B") == "A"
    assert continuity.begin("s", "C") is None
    continuity.finish("s", "B")
    assert continuity.begin("s", "C") is None
    continuity.finish("s", "C")
    continuity.interrupt("s")
    assert continuity.begin("s", "D") is None
    assert continuity.begin("different", "E") is None


@final
class FailingCall:
    def ask(self, preface: str, spoken: str) -> str:
        del preface, spoken
        raise ToolCallFailed("送信後に失敗")


def test_ST_080_003_生成失敗は保留にせず未処理に残す(memories: Memories) -> None:
    _, target = pair(memories)
    clarifying = Clarifying(memories, PAIRS, ToolExplaining(FailingCall()), lambda: AT)
    with pytest.raises(CannotDream, match="送信後"):
        _ = clarifying.run(DWELLER.id)
    assert memories.clarification(target.id, 1) is None
    assert clarifying.result(DWELLER.id).remaining


def test_ST_080_001_別の宿りの同じ出典を根拠にしない(memories: Memories) -> None:
    other = Dweller("other", "other", "別の宿り", "別の宿り")
    memories.settle(other)
    _ = memories.write_identity(other.id, "別の宿りです")
    _ = memories.write_episode(
        other.id, "提案", "公開しますか", 1, AT, source="other-proposal", session_id="session"
    )
    target = memories.write_episode(
        DWELLER.id,
        "いいよ",
        "",
        1,
        AT,
        source="target",
        session_id="session",
        previous_source="other-proposal",
    )
    result = Clarifying(memories, PAIRS, NoCall(), lambda: AT).run(DWELLER.id)
    assert result.records[0].episode_id == target.id
    assert result.records[0].status == "deferred" and not result.records[0].sources


def test_ST_080_002_長い根拠を切り詰めず保留する(memories: Memories) -> None:
    root = memories.write_episode(
        DWELLER.id,
        "長さ上限を越えた相談を記録しておくための発話",
        "あ" * 12000 + "公開は禁止",
        1,
        AT,
        source="long",
        session_id="session",
    )
    target = memories.write_episode(
        DWELLER.id,
        "お願い",
        "",
        1,
        AT,
        source="target",
        session_id="session",
        previous_source=root.source,
    )
    result = Clarifying(memories, PAIRS, NoCall(), lambda: AT).run(DWELLER.id)
    assert result.records[0].episode_id == target.id and result.records[0].status == "deferred"
    assert "切り詰めず" in result.records[0].reason


@final
class UnavailableEmbedding:
    @property
    def name(self) -> str:
        return PAIRS.name

    @property
    def provenance(self) -> Provenance:
        return PAIRS.provenance

    def to_remember(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("数値化に失敗")

    def to_recall(self, text: str) -> Vector:
        return PAIRS.to_recall(text)


def test_ST_080_003_数値化失敗も原文を守り未処理に残す(memories: Memories) -> None:
    _, target = pair(memories)
    with pytest.raises(EmbeddingsUnavailable, match="数値化"):
        _ = Clarifying(
            memories, UnavailableEmbedding(), ToolExplaining(ObservedCall()), lambda: AT
        ).run(DWELLER.id)
    assert memories.clarification(target.id, 1) is None
    assert memories.episode(target.id) == target

"""往復の境界から検索・取得を通し、原文と予算を確認する。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.store import SqliteMemories
from yadori.domain.memory import Dweller, EmbeddingsUnavailable, Provenance, Vector
from yadori.domain.recall.model import RecallFailure
from yadori.infrastructure.memory_mcp import MemoryTools
from yadori.usecase.conversation import Conversation
from yadori.usecase.recall.service import Reading

AT = datetime(2026, 9, 14, tzinfo=UTC)
PERSON = Dweller("sora", "owner", "そら", "そら")
PAIRS = CharacterPairs()
QUERY = "資料の整理方針"


def parsed(text: str) -> dict[str, object]:
    raw: object = json.loads(text)  # pyright: ignore[reportAny]
    assert isinstance(raw, dict)
    return {str(key): value for key, value in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def reference(result: dict[str, object]) -> str:
    rows = result["candidates"]
    assert isinstance(rows, list) and rows
    row: object = rows[0]  # pyright: ignore[reportUnknownVariableType]
    assert isinstance(row, dict) and isinstance(row["reference"], str)
    return row["reference"]


@final
class Fixture:
    def __init__(self, path: Path, reply: str = "見出しごとに整理すると決めました") -> None:
        self.store = SqliteMemories(path / "memories.sqlite")
        self.store.settle(PERSON)
        _ = self.store.write_identity(PERSON.id, "そらです")
        self.episode = Conversation(self.store, PAIRS, lambda: AT).remember(
            PERSON.id, QUERY, reply, source="source", session_id="session"
        )
        self.ledger = RecallLedger(path, PERSON.id)
        self.turn = self.ledger.begin("turn-1", (self.episode.id,))
        self.tools = MemoryTools(self.ledger, Reading(self.store, PAIRS, PERSON.id))


def test_ST_082_002_ST_082_005_候補から原文を読み記憶と回数は増やさない(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        before = fixture.store.episodes_after(PERSON.id, None)
        first = parsed(fixture.tools.search(fixture.turn, QUERY))
        again = parsed(fixture.tools.search(fixture.turn, QUERY))
        assert reference(first) == reference(again)
        assert 'already_suggested":true' in json.dumps(
            first, ensure_ascii=False, separators=(",", ":")
        )
        page = parsed(fixture.tools.get(fixture.turn, reference(first)))
        assert page["complete"] is True and page["next_offset"] is None
        assert "見出しごと" in str(page["text"])
        assert fixture.store.episodes_after(PERSON.id, None) == before
        assert fixture.store.retrieval(fixture.episode.id).count == 0
    finally:
        fixture.store.close()


def test_ST_082_002_IT_082_002_頁の途中に後続が増えても固定本文を返す(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "初め" + "あ" * 2300 + "末尾の条件は公開しない")
    try:
        ref = reference(parsed(fixture.tools.search(fixture.turn, QUERY)))
        first = parsed(fixture.tools.get(fixture.turn, ref))
        assert first["complete"] is False and first["next_offset"] == 1000
        skipped = parsed(fixture.tools.get(fixture.turn, ref, 2000))
        assert skipped["status"] == "invalid_request"
        _ = fixture.store.write_episode(
            PERSON.id,
            "後から増えた会話",
            "含めない",
            1,
            AT,
            source="new",
            session_id="session",
            previous_source="source",
        )
        second = parsed(fixture.tools.get(fixture.turn, ref, 1000))
        last = parsed(fixture.tools.get(fixture.turn, ref, 2000))
        assert last["complete"] is True
        text = str(first["text"]) + str(second["text"]) + str(last["text"])
        assert "公開しない" in text and "後から増えた会話" not in text
    finally:
        fixture.store.close()


def test_ST_082_003_IT_082_001_予約後の失敗と再接続でも予算が戻らない(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        _ = fixture.ledger.reserve(fixture.turn, "search", "予約して終了")
        again = RecallLedger(tmp_path, PERSON.id)
        assert again.begin("turn-1", ()) == fixture.turn
        tools = MemoryTools(again, Reading(fixture.store, PAIRS, PERSON.id))
        assert parsed(tools.search(fixture.turn, ""))["status"] == "invalid_request"
        assert parsed(tools.search(fixture.turn, QUERY))["status"] == "ok"
        assert parsed(tools.search(fixture.turn, QUERY))["status"] == "limit_reached"
        next_turn = again.begin("turn-2", ())
        assert parsed(tools.search(fixture.turn, QUERY))["status"] == "inactive_turn"
        assert parsed(tools.search(next_turn, QUERY))["status"] == "ok"
        again.end("turn-2")
        assert parsed(tools.search(next_turn, QUERY))["status"] == "inactive_turn"
    finally:
        fixture.store.close()


def test_ST_082_003_IT_082_001_並行呼出しでも検索は3回まで(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:

        def call(index: int) -> dict[str, object]:
            del index
            return parsed(fixture.tools.search(fixture.turn, QUERY))

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(call, range(6)))
        assert sum(one["status"] == "ok" for one in results) == 3
        assert sum(one["status"] == "limit_reached" for one in results) == 3
    finally:
        fixture.store.close()


def test_ST_082_003_取得の繰返しにも往復全体の上限が効く(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        ref = reference(parsed(fixture.tools.search(fixture.turn, QUERY)))
        for _ in range(7):
            assert parsed(fixture.tools.get(fixture.turn, ref))["status"] == "ok"
        assert parsed(fixture.tools.get(fixture.turn, ref))["status"] == "limit_reached"
    finally:
        fixture.store.close()


def test_ST_082_003_応答本文は全体12000文字を超えて返さない(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path, "\\" * 10000)
    try:
        search = fixture.tools.search(fixture.turn, QUERY)
        ref = reference(parsed(search))
        total = len(search)
        offset = 0
        for _ in range(7):
            text = fixture.tools.get(fixture.turn, ref, offset)
            result = parsed(text)
            if result["status"] == "limit_reached":
                break
            total += len(text)
            assert result["complete"] is False
            offset += 1000
        else:
            pytest.fail("応答量で停止しなかった")
        assert total <= 12000
    finally:
        fixture.store.close()


def test_ST_082_004_IT_082_001_人物と参照を別の起動へ持ち込めない(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        ref = reference(parsed(fixture.tools.search(fixture.turn, QUERY)))
        with pytest.raises(RecallFailure, match="人物"):
            _ = RecallLedger(tmp_path, "another")
        next_turn = fixture.ledger.begin("turn-2", ())
        assert parsed(fixture.tools.get(next_turn, ref))["status"] == "stale_reference"
        assert parsed(fixture.tools.get(next_turn, "wrong"))["status"] == "invalid_reference"
    finally:
        fixture.store.close()


@pytest.mark.parametrize(
    "query,limit", [(" ", 3), ("a" * 121, 3), (QUERY, 0), (QUERY, 4), (QUERY, True)]
)
def test_ST_082_006_不正な検索を空の結果と区別する(tmp_path: Path, query: str, limit: int) -> None:
    fixture = Fixture(tmp_path)
    try:
        assert (
            parsed(fixture.tools.search(fixture.turn, query, limit))["status"] == "invalid_request"
        )
        assert parsed(fixture.tools.search(fixture.turn, "z" * 120))["status"] == "no_results"
    finally:
        fixture.store.close()


@final
class Unavailable:
    @property
    def name(self) -> str:
        return PAIRS.name

    @property
    def provenance(self) -> Provenance:
        return PAIRS.provenance

    def to_remember(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("使えない")

    def to_recall(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("使えない")


def test_ST_082_006_検索不能を空の記憶としない(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        tools = MemoryTools(fixture.ledger, Reading(fixture.store, Unavailable(), PERSON.id))
        result = parsed(tools.search(fixture.turn, QUERY))
        assert result["status"] == "unavailable" and result["searches_left"] == 2
    finally:
        fixture.store.close()


def test_ST_082_003_未取得のまま上限に達したら続きを約束させる位置を返さない(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path, "あ" * 9000)
    try:
        ref = reference(parsed(fixture.tools.search(fixture.turn, QUERY)))
        for offset in range(0, 6000, 1000):
            assert parsed(fixture.tools.get(fixture.turn, ref, offset))["status"] == "ok"
        last = parsed(fixture.tools.get(fixture.turn, ref, 6000))
        assert last["status"] == "limit_reached"
        assert last["complete"] is False and last["next_offset"] is None
        assert "引き継げません" in str(last["message"])
    finally:
        fixture.store.close()


def test_ST_082_002_候補数と抜粋を制限し続きの有無を示す(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    try:
        conversation = Conversation(fixture.store, PAIRS, lambda: AT)
        for index in range(4):
            _ = conversation.remember(PERSON.id, QUERY * 20 + str(index), "長い原文")
        result = parsed(fixture.tools.search(fixture.turn, QUERY))
        rows = result["candidates"]
        assert isinstance(rows, list)
        assert result["has_more"] is True
        values = [parsed(json.dumps(one)) for one in rows]  # pyright: ignore[reportUnknownVariableType]
        assert len(values) == 3
        assert all(len(str(one["excerpt"])) <= 120 for one in values)
        assert any(one["excerpt_truncated"] is True for one in values)
    finally:
        fixture.store.close()

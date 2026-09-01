"""INC-025 の結合テスト。埋め込みを替えたときの境界を確かめる。

架空の会話で書く。意味を見る埋め込みは AIモデルを呼んで遅いため、それそのものではなく、
埋め込みが変わったときの振る舞いを確かめる。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.memory import Dweller, EmbeddingsUnavailable, HowToRecall, Provenance, Vector
from yadori.usecase.conversation import Conversation

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
KEPT = [
    "ベランダにトマトの苗を植えました",
    "住民税の納付書が届きました",
    "図書館で小説を三冊借りました",
    "昨日は古い映画を観ました",
]
HOW = HowToRecall(recent_turns=1, found_limit=5, relevance_floor=0.15)


@final
class _Reversed:
    """別の埋め込み。文字の並びを逆にしてから作るため、同じ値にならない。"""

    def __init__(self, inner: CharacterPairs) -> None:
        self._inner: CharacterPairs = inner

    @property
    def provenance(self) -> Provenance:
        return Provenance(ai_model=None, tool="reversed-characters", tool_version="v1")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_recall(self, text: str) -> Vector:

        return self.to_remember(text)

    def to_remember(self, text: str) -> Vector:
        return self._inner.to_remember(text[::-1])


@final
class _Missing:
    """使えない埋め込み。"""

    @property
    def provenance(self) -> Provenance:
        return Provenance(ai_model=None, tool="missing", tool_version="v0")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_recall(self, text: str) -> Vector:

        return self.to_remember(text)

    def to_remember(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable("入れてください")


@final
class _Ticking:
    def __init__(self) -> None:
        self._at: datetime = datetime(2000, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at


class TestChangingTheModel:
    def _filled(self) -> SqliteMemories:
        memories = SqliteMemories(":memory:")
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        first = Conversation(memories, CharacterPairs(), _Ticking(), HOW)
        for utterance in KEPT:
            _ = first.remember(SORA.id, utterance, "はい")
        return memories

    # IT-025-001 違う埋め込みのインデックスを混ぜない

    def test_IT_025_001_替えた直後は探した記憶が空になり落ちない(self) -> None:
        memories = self._filled()
        second = Conversation(memories, _Reversed(CharacterPairs()), _Ticking(), HOW)

        recollected = second.recall(SORA.id, "トマトはその後どうなりましたか")

        assert recollected.found == ()
        assert memories.count_episodes(SORA.id) == len(KEPT)
        memories.close()

    def test_IT_025_001_作り直すと以前の記憶も新しい埋め込みで探せる(self) -> None:
        memories = self._filled()
        second = Conversation(memories, _Reversed(CharacterPairs()), _Ticking(), HOW)

        rebuilt = second.rebuild_index(SORA.id)

        assert rebuilt == len(KEPT)
        assert memories.count_episodes(SORA.id) == len(KEPT)
        assert second.recall(SORA.id, "トマトはその後どうなりましたか").found != ()
        # 作り直した後は、いまの埋め込みのインデックスを持たない記憶が無い。
        assert memories.episodes_without_index(SORA.id, "reversed-characters-v1") == ()
        # 以前の埋め込みのインデックスは消えていない。
        assert memories.episodes_without_index(SORA.id, "character-pairs-v1") == ()
        memories.close()

    # IT-025-002 使えなければ書く前に断る

    def test_IT_025_002_使えなければ思い出す前に断り記憶も増えない(self) -> None:
        memories = self._filled()
        kept = memories.count_episodes(SORA.id)
        broken = Conversation(memories, _Missing(), _Ticking(), HOW)

        with pytest.raises(EmbeddingsUnavailable):
            _ = broken.recall(SORA.id, "トマトはどうなりましたか")
        with pytest.raises(EmbeddingsUnavailable):
            _ = broken.remember(SORA.id, "新しい話", "はい")

        assert memories.count_episodes(SORA.id) == kept
        # インデックスも増えていない。使えない道のインデックスを持つ記憶は一件も無い。
        assert len(memories.episodes_without_index(SORA.id, "missing")) == kept
        memories.close()

    # IT-025-003 道を選んで測れる

    def test_IT_025_003_二つの道から渡すとどちらで出たかを読める(self) -> None:
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        ways = [CharacterPairs(), _Reversed(CharacterPairs())]
        both = Conversation(memories, ways, _Ticking(), HOW)
        for utterance in KEPT:
            _ = both.remember(SORA.id, utterance, "はい")

        found = both.recall(SORA.id, "トマトはその後どうなりましたか").found

        assert found != ()
        # 両方の道から出ている。片方が空でも通る判定にしない。
        assert {one.way for one in found} == {"character-pairs-v1", "reversed-characters-v1"}
        # 同じ記憶が二つの道で出ても、渡すのは一度だけ。
        assert len({one.episode.id for one in found}) == len(found)


class TestOpeningAnOlderStore:
    """以前の版が作った保存先を、いまの版で開く。"""

    OLD_SCHEMA: str = """
    CREATE TABLE dweller (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
        nickname TEXT NOT NULL);
    CREATE TABLE identity (dweller_id TEXT NOT NULL REFERENCES dweller(id),
        version INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (dweller_id, version));
    CREATE TABLE episode (id INTEGER PRIMARY KEY AUTOINCREMENT,
        dweller_id TEXT NOT NULL REFERENCES dweller(id), utterance TEXT NOT NULL,
        reply TEXT NOT NULL, identity_version INTEGER NOT NULL, happened_at TEXT NOT NULL);
    CREATE TABLE episode_index (episode_id INTEGER PRIMARY KEY REFERENCES episode(id),
        model TEXT NOT NULL, vector TEXT NOT NULL);
    CREATE TABLE retrieval (episode_id INTEGER NOT NULL REFERENCES episode(id),
        at TEXT NOT NULL);
    """

    def _older(self, path: Path) -> None:
        """以前の版の形で、記憶とインデックスを持つ保存先を作る。"""
        connection = sqlite3.connect(path)
        _ = connection.executescript(self.OLD_SCHEMA)
        _ = connection.execute(
            "INSERT INTO dweller VALUES (?, ?, ?, ?)",
            (SORA.id, SORA.owner, SORA.name, SORA.nickname),
        )
        _ = connection.execute(
            "INSERT INTO identity VALUES (?, 1, ?)", (SORA.id, "わたしはそらです。")
        )
        for utterance in KEPT:
            cursor = connection.execute(
                "INSERT INTO episode (dweller_id, utterance, reply, identity_version, happened_at)"
                + " VALUES (?, ?, ?, 1, '2000-01-01T00:00:00+00:00')",
                (SORA.id, utterance, "はい"),
            )
            _ = connection.execute(
                "INSERT INTO episode_index VALUES (?, 'character-pairs-v1', '[0.0]')",
                (cursor.lastrowid,),
            )
        connection.commit()
        connection.close()

    # IT-025-004 以前の形の保存先を開いても原文は残り、インデックスは埋め込みごとに持てる

    def test_IT_025_004_以前の形の保存先を開くと原文は残りインデックスは埋め込みごとに持てる(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "memories.sqlite"
        self._older(path)

        memories = SqliteMemories(path)

        assert memories.count_episodes(SORA.id) == len(KEPT)
        # 以前のインデックスは捨てられ、いまの埋め込みのインデックスを持たない記憶は全件になる。
        assert len(memories.episodes_without_index(SORA.id, "character-pairs-v1")) == len(KEPT)
        first = memories.recent(SORA.id, 1)[0]
        memories.write_index(first.id, "character-pairs-v1", (1.0, 0.0))
        memories.write_index(first.id, "reversed-characters-v1", (0.0, 1.0))
        # 二つの埋め込みのインデックスが同じ記憶に両方残る。片方が黙って上書きされない。
        others = {episode.id for episode in memories.recent(SORA.id, 10)} - {first.id}
        for model in ("character-pairs-v1", "reversed-characters-v1"):
            without = {one.id for one in memories.episodes_without_index(SORA.id, model)}
            assert without == others
        memories.close()

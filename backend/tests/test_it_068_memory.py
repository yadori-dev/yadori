"""INC-068 の記憶側の結合テスト。出典、思い出した時刻、移行、一度だけの動き。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.memory import Dweller, Memories, Moved, RememberingConflict
from yadori.usecase.conversation import Conversation

SORA = Dweller("sora", "架空の持ち主", "そら", "そら")
AT = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)


def _settled(memories: Memories) -> Conversation:
    memories.settle(SORA)
    _ = memories.write_identity(SORA.id, "最初の名乗り")
    return Conversation(memories, CharacterPairs(), lambda: AT + timedelta(minutes=1))


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_IT_068_003_同じ出典は一往復と一つの動きに限る(kind: str, tmp_path: Path) -> None:
    memories: Memories = (
        InMemoryMemories() if kind == "memory" else SqliteMemories(tmp_path / "memories.sqlite")
    )
    conversation = _settled(memories)

    first = conversation.remember(
        SORA.id,
        "前の決定は？",
        "原文を残します",
        Moved(0.2, "思い出せた"),
        recalled_at=AT,
        source="claude:prompt-1",
        identity_version=1,
    )
    replayed = conversation.remember(
        SORA.id,
        "前の決定は？",
        "原文を残します",
        Moved(0.2, "思い出せた"),
        recalled_at=AT,
        source="claude:prompt-1",
        identity_version=1,
    )

    assert first == replayed
    assert first.recalled_at == AT and first.source == "claude:prompt-1"
    assert memories.count_episodes(SORA.id) == 1
    assert len(memories.shifts(SORA.id)) == 1

    with pytest.raises(RememberingConflict):
        _ = conversation.remember(
            SORA.id,
            "前の決定は？",
            "原文を残します",
            Moved(-0.2, "違う動き"),
            recalled_at=AT,
            source="claude:prompt-1",
            identity_version=1,
        )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
@pytest.mark.parametrize("first_has_moved", [False, True])
def test_IT_068_003_同じ出典は気持ちの有無が変わる再送を断る(
    kind: str, first_has_moved: bool, tmp_path: Path
) -> None:
    memories: Memories = (
        InMemoryMemories() if kind == "memory" else SqliteMemories(tmp_path / "memories.sqlite")
    )
    conversation = _settled(memories)
    moved = Moved(0.2, "覚えた")
    _ = conversation.remember(
        SORA.id,
        "前の決定は？",
        "原文を残します",
        moved if first_has_moved else None,
        recalled_at=AT,
        source="claude:moved-presence",
        identity_version=1,
    )

    with pytest.raises(RememberingConflict):
        _ = conversation.remember(
            SORA.id,
            "前の決定は？",
            "原文を残します",
            None if first_has_moved else moved,
            recalled_at=AT,
            source="claude:moved-presence",
            identity_version=1,
        )

    assert memories.count_episodes(SORA.id) == 1
    assert len(memories.shifts(SORA.id)) == (1 if first_has_moved else 0)
    if isinstance(memories, SqliteMemories):
        memories.close()


def test_IT_068_003_思い出した後に名乗りが変わっても前の版で覚える() -> None:
    memories = InMemoryMemories()
    conversation = _settled(memories)
    recollection = conversation.recall(SORA.id, "前の決定は？")
    _ = memories.write_identity(SORA.id, "新しい名乗り")

    episode = conversation.remember(
        SORA.id,
        "前の決定は？",
        "原文を残します",
        recalled_at=recollection.recalled_at,
        source="claude:prompt-2",
        identity_version=recollection.identity.version,
    )

    assert episode.identity_version == 1
    assert episode.recalled_at == recollection.recalled_at


def test_IT_068_003_以前の保存先へ不明の列だけを足して原文を変えない(tmp_path: Path) -> None:
    path = tmp_path / "memories.sqlite"
    connection = sqlite3.connect(path)
    _ = connection.executescript(
        """
        CREATE TABLE dweller (id TEXT PRIMARY KEY, owner TEXT, name TEXT, nickname TEXT);
        CREATE TABLE identity (dweller_id TEXT, version INTEGER, text TEXT,
          PRIMARY KEY (dweller_id, version));
        CREATE TABLE episode (id INTEGER PRIMARY KEY AUTOINCREMENT, dweller_id TEXT,
          utterance TEXT, reply TEXT, identity_version INTEGER, happened_at TEXT);
        CREATE TABLE shift (id INTEGER PRIMARY KEY AUTOINCREMENT, dweller_id TEXT, at TEXT,
          delta REAL, cause TEXT, episode_id INTEGER);
        INSERT INTO dweller VALUES ('sora', '持ち主', 'そら', 'そら');
        INSERT INTO identity VALUES ('sora', 1, '名乗り');
        INSERT INTO episode VALUES (1, 'sora', '元の発話', '元の返事', 1,
          '2026-09-01T00:00:00+00:00');
        """
    )
    connection.close()

    memories = SqliteMemories(path)
    try:
        episode = memories.episode(1)
    finally:
        memories.close()

    assert episode is not None
    assert episode.utterance == "元の発話" and episode.reply == "元の返事"
    assert episode.recalled_at is None and episode.source is None


def test_IT_068_003_気持ちの保存に失敗した一往復は原文だけでも残さない(tmp_path: Path) -> None:
    path = tmp_path / "memories.sqlite"
    memories = SqliteMemories(path)
    conversation = _settled(memories)
    connection = sqlite3.connect(path)
    _ = connection.execute(
        "CREATE TRIGGER refuse_shift BEFORE INSERT ON shift"
        + " BEGIN SELECT RAISE(ABORT, '動きを保存できない'); END"
    )
    connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="動きを保存できない"):
        _ = conversation.remember(
            SORA.id,
            "保存できる？",
            "途中では残しません",
            Moved(0.2, "確かめた"),
            recalled_at=AT,
            source="claude:failed-prompt",
            identity_version=1,
        )

    assert memories.count_episodes(SORA.id) == 0
    assert memories.shifts(SORA.id) == ()
    memories.close()


def test_IT_068_003_索引の保存に失敗しても原文と気持ちを二重にせず再送で補う(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memories.sqlite"
    memories = SqliteMemories(path)
    conversation = _settled(memories)
    connection = sqlite3.connect(path)
    _ = connection.execute(
        "CREATE TRIGGER refuse_index BEFORE INSERT ON episode_index"
        + " BEGIN SELECT RAISE(ABORT, '索引を保存できない'); END"
    )
    connection.close()
    with pytest.raises(sqlite3.IntegrityError, match="索引を保存できない"):
        _ = conversation.remember(
            SORA.id,
            "索引は補える？",
            "原文から補います",
            Moved(0.1, "復旧できる"),
            recalled_at=AT,
            source="claude:index-retry",
            identity_version=1,
        )

    assert memories.count_episodes(SORA.id) == 1
    assert len(memories.shifts(SORA.id)) == 1
    assert len(memories.episodes_without_index(SORA.id, CharacterPairs().name)) == 1

    connection = sqlite3.connect(path)
    _ = connection.execute("DROP TRIGGER refuse_index")
    connection.close()
    replayed = conversation.remember(
        SORA.id,
        "索引は補える？",
        "原文から補います",
        Moved(0.1, "復旧できる"),
        recalled_at=AT,
        source="claude:index-retry",
        identity_version=1,
    )

    assert replayed.id == 1
    assert memories.count_episodes(SORA.id) == 1
    assert len(memories.shifts(SORA.id)) == 1
    assert memories.episodes_without_index(SORA.id, CharacterPairs().name) == ()
    memories.close()

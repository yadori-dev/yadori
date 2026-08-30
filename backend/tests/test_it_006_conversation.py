"""INC-006 の結合テスト。思い出すと覚えるを確かめる。

構造を選んだ理由を確かめる。動くことだけを見ない。架空の宿りで書き、
利用者の実際の会話は使わない。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import final

import pytest

from tests.sora import (
    ABOUT_TOMATO,
    FILLERS,
    HOW,
    NAME_DECLARED,
    PLANTED,
    POINTING,
    RECENT,
    SORA,
    UNRELATED,
    Ticking,
    talk,
)
from yadori.adapter.embedding.characters import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.memory import Episode, Memories, NameNotDeclared, Vector
from yadori.usecase.conversation import Conversation


@final
class _FailingWrite:
    """原文を書く段で必ず失敗する保存先。ほかの操作は本物へ渡す。"""

    def __init__(self, inner: SqliteMemories) -> None:
        self._inner: SqliteMemories = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)  # pyright: ignore[reportAny]

    def write_episode(self, *args: object, **kwargs: object) -> Episode:
        raise OSError("保存先を読み書きできない")


@final
class _FailingIndex:
    """索引を書く段でだけ失敗する保存先。原文は本物へ書く。"""

    def __init__(self, inner: SqliteMemories) -> None:
        self._inner: SqliteMemories = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)  # pyright: ignore[reportAny]

    def write_index(self, *args: object, **kwargs: object) -> None:
        raise OSError("索引を書けない")


@final
class _SwappedEmbeddings:
    """別の作りの模型。文字一つずつを見るため、二つ組の実装とは違う結果になる。"""

    @property
    def name(self) -> str:
        return "single-characters-v1"

    def of(self, text: str) -> Vector:
        counts: list[float] = [0.0] * 512
        for character in "".join(text.split()):
            counts[hash(character) % 512] += 1.0
        length = math.sqrt(sum(count * count for count in counts))
        return tuple(count / length if length else 0.0 for count in counts)


@pytest.fixture
def sqlite_memories(tmp_path: Path) -> Iterator[SqliteMemories]:
    memories = SqliteMemories(tmp_path / "test.sqlite")
    yield memories
    memories.close()


def settle(memories: SqliteMemories | InMemoryMemories, *, declare_name: bool = True) -> None:
    memories.settle(SORA)
    if declare_name:
        memories.write_identity(SORA.id, NAME_DECLARED)


def make(memories: Memories, embeddings: object | None = None) -> Conversation:
    chosen = embeddings if embeddings is not None else CharacterPairs()
    return Conversation(memories, chosen, Ticking(), HOW)  # pyright: ignore[reportArgumentType]


# IT-006-001 外の技術を差し替えても記憶の規則が変わらない


def _sequence(conversation: Conversation) -> tuple[list[str], list[str]]:
    talk(conversation, [PLANTED, *FILLERS])
    recollection = conversation.recall(SORA.id, ABOUT_TOMATO)
    return (
        [episode.utterance for episode in recollection.recent],
        [one.episode.utterance for one in recollection.found],
    )


def test_IT_006_001_保存を差し替えても返る記憶と順序が変わらない(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    on_sqlite = _sequence(make(sqlite_memories))

    other = InMemoryMemories()
    settle(other)
    on_memory = _sequence(make(other))

    assert on_sqlite == on_memory
    assert sqlite_memories.count_episodes(SORA.id) == other.count_episodes(SORA.id)


def test_IT_006_001_模型を差し替えても規則は変わる_探した中身だけが変わりうる(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories, _SwappedEmbeddings())
    talk(conversation, [PLANTED, *FILLERS])

    recollection = conversation.recall(SORA.id, ABOUT_TOMATO)

    # 模型が変われば探し当てる記憶は変わりうる。変わらないのは規則のほう。
    assert [episode.utterance for episode in recollection.recent] == RECENT
    recent_ids = {episode.id for episode in recollection.recent}
    assert not recent_ids & {one.episode.id for one in recollection.found}
    assert recollection.identity.text == NAME_DECLARED


# IT-006-002 直近と探した記憶が別の道で来る


def test_IT_006_002_指す語だけの発話では直近だけが埋まる(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories)
    talk(conversation, [PLANTED, *FILLERS])

    recollection = conversation.recall(SORA.id, POINTING)

    assert [episode.utterance for episode in recollection.recent] == RECENT
    assert recollection.found == ()


def test_IT_006_002_直近に無い話題は意味で探して足され直近と重ならない(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories)
    talk(conversation, [PLANTED, *FILLERS])

    recollection = conversation.recall(SORA.id, ABOUT_TOMATO)

    assert PLANTED in [one.episode.utterance for one in recollection.found]
    assert PLANTED not in [episode.utterance for episode in recollection.recent]
    recent_ids = {episode.id for episode in recollection.recent}
    assert not recent_ids & {one.episode.id for one in recollection.found}


def test_IT_006_002_どの記憶とも近くない発話では探した記憶が空になる(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories)
    talk(conversation, [PLANTED, *FILLERS])

    recollection = conversation.recall(SORA.id, UNRELATED)

    assert recollection.found == ()
    assert len(recollection.recent) == 6


def test_IT_006_002_近さと思い出した記録を別々に読め呼ぶたび記録が増える(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories)
    talk(conversation, [PLANTED, *FILLERS])

    first = conversation.recall(SORA.id, ABOUT_TOMATO)
    second = conversation.recall(SORA.id, ABOUT_TOMATO)

    assert first.found[0].relevance >= HOW.relevance_floor
    # 一度目は思い出す前の記録を見るため零。二度目に一度目の記録が現れる。
    assert first.found[0].retrieval.count == 0
    assert second.found[0].retrieval.count == 1
    assert second.found[0].retrieval.last_at is not None


# IT-006-003 原文が索引と失敗から独立している


def test_IT_006_003_索引を消して作り直しても原文と結果が変わらない(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    conversation = make(sqlite_memories)
    talk(conversation, [PLANTED, *FILLERS])
    before = conversation.recall(SORA.id, ABOUT_TOMATO)
    kept = sqlite_memories.count_episodes(SORA.id)

    sqlite_memories.clear_index(SORA.id)
    emptied = conversation.recall(SORA.id, ABOUT_TOMATO)
    rebuilt = conversation.rebuild_index(SORA.id)
    after = conversation.recall(SORA.id, ABOUT_TOMATO)

    assert sqlite_memories.count_episodes(SORA.id) == kept
    assert emptied.found == ()
    assert rebuilt == kept
    assert [one.episode.id for one in after.found] == [one.episode.id for one in before.found]


def test_IT_006_003_覚える途中で失敗すると何も増えない(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    talk(make(sqlite_memories), [PLANTED])
    kept = sqlite_memories.count_episodes(SORA.id)

    failing: Memories = _FailingWrite(sqlite_memories)  # pyright: ignore[reportAssignmentType]
    with pytest.raises(OSError):
        make(failing).remember(SORA.id, "覚えられない発話", "返事")

    assert sqlite_memories.count_episodes(SORA.id) == kept
    assert sqlite_memories.retrieval(1).count == 0
    current = sqlite_memories.current_identity(SORA.id)
    assert current is not None
    assert current.version == 1


def test_IT_006_003_索引を書けなくても原文は残り後から作り直せる(
    sqlite_memories: SqliteMemories,
) -> None:
    settle(sqlite_memories)
    failing: Memories = _FailingIndex(sqlite_memories)  # pyright: ignore[reportAssignmentType]
    with pytest.raises(OSError):
        make(failing).remember(SORA.id, PLANTED, "いいですね")
    # 直近から押し出さないと、意味で探す側に現れない。
    talk(make(sqlite_memories), FILLERS)

    # 原文は残る。索引は作り直せる派生物なので、失われても記憶は失われない。
    assert sqlite_memories.count_episodes(SORA.id) == 1 + len(FILLERS)
    conversation = make(sqlite_memories)
    assert conversation.rebuild_index(SORA.id) == 1
    assert conversation.recall(SORA.id, ABOUT_TOMATO).found[0].episode.utterance == PLANTED


def test_IT_006_003_名乗りが無ければ書く前に断る(sqlite_memories: SqliteMemories) -> None:
    settle(sqlite_memories, declare_name=False)
    conversation = make(sqlite_memories)

    with pytest.raises(NameNotDeclared):
        conversation.recall(SORA.id, "はじめまして")
    with pytest.raises(NameNotDeclared):
        conversation.remember(SORA.id, "はじめまして", "返事")

    assert sqlite_memories.count_episodes(SORA.id) == 0

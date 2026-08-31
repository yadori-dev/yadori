"""INC-025 の結合テスト。埋め込みを替えたときの境界を確かめる。

架空の会話で書く。模型を呼ぶと遅いため、模型そのものではなく、模型が
変わったときの振る舞いを確かめる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.memory import Dweller, EmbeddingsUnavailable, HowToRecall, Vector
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
    """別の模型。文字の並びを逆にしてから作るため、同じ値にならない。"""

    def __init__(self, inner: CharacterPairs) -> None:
        self._inner: CharacterPairs = inner

    @property
    def name(self) -> str:
        return "reversed-characters-v1"

    def of(self, text: str) -> Vector:
        return self._inner.of(text[::-1])


@final
class _Missing:
    """使えない埋め込み。"""

    @property
    def name(self) -> str:
        return "missing"

    def of(self, text: str) -> Vector:
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

    # IT-025-001 違う模型の索引を混ぜない

    def test_IT_025_001_替えた直後は探した記憶が空になり落ちない(self) -> None:
        memories = self._filled()
        second = Conversation(memories, _Reversed(CharacterPairs()), _Ticking(), HOW)

        recollected = second.recall(SORA.id, "トマトはその後どうなりましたか")

        assert recollected.found == ()
        assert memories.count_episodes(SORA.id) == len(KEPT)
        memories.close()

    def test_IT_025_001_作り直すと以前の記憶も新しい模型で探せる(self) -> None:
        memories = self._filled()
        second = Conversation(memories, _Reversed(CharacterPairs()), _Ticking(), HOW)

        rebuilt = second.rebuild_index(SORA.id)

        assert rebuilt == len(KEPT)
        assert memories.count_episodes(SORA.id) == len(KEPT)
        assert second.recall(SORA.id, "トマトはその後どうなりましたか").found != ()
        # 作り直した後は、いまの模型の索引を持たない記憶が無い。
        assert memories.episodes_without_index(SORA.id, "reversed-characters-v1") == ()
        # 以前の模型の索引は消えていない。
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
        assert {one.by for one in found} <= {"character-pairs-v1", "reversed-characters-v1"}
        # 同じ記憶が二つの道で出ても、渡すのは一度だけ。
        assert len({one.episode.id for one in found}) == len(found)

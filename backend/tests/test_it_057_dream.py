"""INC-057 の結合テスト。

選ぶ規則の境界と繰り返しの見方、要点と記録となぞりの結び、返りの形、要点が前置きに流れること。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.dream import ClaudeCodeSummarizing
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.adapter.voice import ClaudeCodeVoice
from yadori.domain.dream import MOVED_ENOUGH, Candidate, CannotDream, Keeping, Summarized
from yadori.domain.memory import (
    Character,
    Dweller,
    Episode,
    Gist,
    HowToRecall,
    Identity,
    Memories,
    Mood,
    Provenance,
    Recollection,
    Shift,
    State,
    Vector,
)
from yadori.usecase.conversation import Conversation
from yadori.usecase.dream import Dreaming, Dreamt, NothingKept

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
AT = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
PAIRS = CharacterPairs()


@final
class _Answering:
    def __init__(self, answered: str) -> None:
        self._answered: str = answered
        self.prefaces: list[str] = []
        self.spoken: list[str] = []

    def ask(self, preface: str, spoken: str) -> str:
        self.prefaces.append(preface)
        self.spoken.append(spoken)
        return self._answered


@final
class _Summarizing:
    def __init__(self, gists: tuple[str, ...] = ("要点",), noticing: str | None = None) -> None:
        self._gists: tuple[str, ...] = gists
        self._noticing: str | None = noticing
        self.read: list[tuple[Episode, ...]] = []

    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        del identity
        self.read.append(tuple(episodes))
        return Summarized(self._gists, self._noticing)


@final
class _Failing:
    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        del identity, episodes
        raise CannotDream("応えない")


@final
class _Sided:
    """覚える側と問い合わせ側で違う並びを返す埋め込み。

    覚える側は「植」を持つ文と「水」を持つ文を直交する向きに置き、問い合わせ側はその逆に置く。
    つまり「植」の文で問い合わせると「水」の文の覚える側に重なり、覚える側どうしは重ならない。
    """

    @property
    def provenance(self) -> Provenance:
        return Provenance(None, "sided", "v1")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_remember(self, text: str) -> Vector:
        return (1.0, 0.0) if "植" in text else (0.0, 1.0) if "水" in text else (0.0, 0.0)

    def to_recall(self, text: str) -> Vector:
        return (0.0, 1.0) if "植" in text else (1.0, 0.0) if "水" in text else (0.0, 0.0)


def _episode(number: int, said: str = "x", minutes: int = 0) -> Episode:
    return Episode(number, said, "はい", 1, AT + timedelta(minutes=minutes))


def _settled(memories: Memories) -> None:
    memories.settle(SORA)
    _ = memories.write_identity(SORA.id, "わたしはそらです。")


def _ticking() -> Callable[[], datetime]:
    clock = [AT]

    def now() -> datetime:
        clock[0] += timedelta(minutes=1)
        return clock[0]

    return now


class TestIT057001:
    """選ぶ規則の境界と、繰り返しの見方。"""

    @pytest.mark.parametrize(
        ("retrieved", "shift", "repeated", "kept"),
        [
            (False, 0.0, False, False),
            (True, 0.0, False, True),
            (False, MOVED_ENOUGH - 0.01, False, False),
            (False, MOVED_ENOUGH, False, True),
            (False, -MOVED_ENOUGH, False, True),
            (False, 0.0, True, True),
        ],
    )
    def test_IT_057_001_思い出された動いた繰り返したのいずれかで選ばれる(
        self, retrieved: bool, shift: float, repeated: bool, kept: bool
    ) -> None:
        candidate = Candidate(_episode(1), retrieved, shift, repeated)

        assert Keeping().keep([candidate]) == ((_episode(1),) if kept else ())

    def test_IT_057_001_前回の終わりと同じ時刻は読まず繰り返しは範囲の中だけで数える(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        now = _ticking()
        conversation = Conversation(memories, PAIRS, now)
        for said in ("トマトの苗を植えました", "トマトの苗に水をやりました", "為替の見通し"):
            _ = conversation.remember(SORA.id, said, "はい")
        first = memories.episodes_after(SORA.id, None)[0]
        _ = memories.record_dream(SORA.id, AT, AT, first.happened_at, 1, 0, None)
        summarizing = _Summarizing()

        nothing = Dreaming(memories, PAIRS, summarizing, now, HowToRecall(6, 5, 0.3)).run(SORA.id)

        # 一件目と同じ時刻は読まず、二件目と三件目だけを読む。二件目は一件目と同じ話題だが、
        # 一件目は今回の範囲の外なので繰り返しに数えず、残すものは無い。
        assert isinstance(nothing, NothingKept)
        assert nothing.dream.count == 2 and nothing.dream.kept == 0
        assert summarizing.read == []

    def test_IT_057_001_繰り返しは問い合わせ側で見る(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        now = _ticking()
        conversation = Conversation(memories, _Sided(), now)
        _ = conversation.remember(SORA.id, "トマトの苗を植えました", "はい")
        _ = conversation.remember(SORA.id, "トマトの苗に水をやりました", "はい")

        dreamt = Dreaming(memories, _Sided(), _Summarizing(), now, HowToRecall(6, 5, 0.9)).run(
            SORA.id
        )

        # 問い合わせ側で見れば互いに相手を思い出すので両方が選ばれる。覚える側どうしでは直交する。
        assert isinstance(dreamt, Dreamt) and dreamt.dream.kept == 2

    def test_IT_057_001_範囲の外に古い同じ話題が多くても範囲の中の対は選ばれる(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        now = _ticking()
        conversation = Conversation(memories, PAIRS, now)
        for _ in range(10):
            _ = conversation.remember(SORA.id, "トマトの苗を植えました", "はい")
        last_old = memories.episodes_after(SORA.id, None)[-1]
        _ = memories.record_dream(SORA.id, AT, AT, last_old.happened_at, 10, 0, None)
        _ = conversation.remember(SORA.id, "トマトの苗を植えました", "はい")
        _ = conversation.remember(SORA.id, "トマトの苗に水をやりました", "はい")

        dreamt = Dreaming(memories, PAIRS, _Summarizing(), now, HowToRecall(6, 5, 0.3)).run(SORA.id)

        assert isinstance(dreamt, Dreamt) and dreamt.dream.count == 2 and dreamt.dream.kept == 2


class TestIT057002:
    """要点と記録となぞりが結ばれ、失敗で何も積まれない。"""

    @pytest.mark.parametrize("kind", ["inmemory", "sqlite"])
    def test_IT_057_002_要点は夢と往復に結ばれ選んだ往復だけがなぞられる(
        self, kind: str, tmp_path: Path
    ) -> None:
        memories: Memories = (
            InMemoryMemories() if kind == "inmemory" else SqliteMemories(tmp_path / "m.sqlite")
        )
        _settled(memories)
        kept = memories.write_episode(SORA.id, "やっと通った", "はい", 1, AT)
        other = memories.write_episode(
            SORA.id, "洗濯物がよく乾きました", "はい", 1, AT + timedelta(minutes=1)
        )
        for episode in (kept, other):
            memories.write_index(episode.id, PAIRS.name, PAIRS.to_remember(episode.utterance))
        memories.record_shift(SORA.id, Shift(AT, 0.5, "ほっとした", kept.id))
        summarizing = _Summarizing(("原因はタイポ", "三時間かかった"), "急ぐと見落とす")

        dreamt = Dreaming(memories, PAIRS, summarizing, lambda: AT + timedelta(hours=8)).run(
            SORA.id
        )

        assert isinstance(dreamt, Dreamt)
        assert dreamt.dream.kept == 1 and dreamt.dream.count == 2
        gists = memories.gists_of_dream(dreamt.dream.id)
        assert [one.text for one in gists] == ["原因はタイポ", "三時間かかった"]
        assert all(one.sources == (kept.id,) and one.made_at == dreamt.dream.at for one in gists)
        assert memories.retrieval(kept.id).count == 1
        assert memories.retrieval(kept.id).last_at == AT + timedelta(hours=8)
        assert memories.retrieval(other.id).count == 0
        assert dreamt.dream.noticing == "急ぐと見落とす"
        if isinstance(memories, SqliteMemories):
            memories.close()

    def test_IT_057_002_失敗では記録も要点も思い出した記録も原文も変わらない(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        kept = memories.write_episode(SORA.id, "やっと通った", "はい", 1, AT)
        memories.write_index(kept.id, PAIRS.name, PAIRS.to_remember(kept.utterance))
        memories.record_shift(SORA.id, Shift(AT, 0.5, "ほっとした", kept.id))

        with pytest.raises(CannotDream):
            _ = Dreaming(memories, PAIRS, _Failing(), lambda: AT).run(SORA.id)

        assert memories.latest_dream(SORA.id) is None
        assert memories.retrieval(kept.id).count == 0
        assert memories.count_episodes(SORA.id) == 1

    def test_IT_057_002_一度に読むのは上限までで残りは次の夢が読む(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        now = _ticking()
        conversation = Conversation(memories, PAIRS, now)
        for number in range(105):
            _ = conversation.remember(SORA.id, f"話題{number}", "はい")

        first = Dreaming(memories, PAIRS, _Summarizing(), now, HowToRecall(6, 5, 0.99)).run(SORA.id)
        second = Dreaming(memories, PAIRS, _Summarizing(), now, HowToRecall(6, 5, 0.99)).run(
            SORA.id
        )

        assert isinstance(first, NothingKept) and first.dream.count == 100
        assert isinstance(second, NothingKept) and second.dream.count == 5


class TestIT057003:
    """返りの形の切り出し。"""

    @pytest.mark.parametrize(
        ("answered", "gists", "noticing"),
        [
            (
                "- 原因はタイポ\n- 三時間かかった\n気づき: 急ぐと見落とす",
                ("原因はタイポ", "三時間かかった"),
                "急ぐと見落とす",
            ),
            ("・原因はタイポ\n気づき: 無し", ("原因はタイポ",), None),
            ("前置き。\n- 原因はタイポ\n\n気づき:", ("原因はタイポ",), None),
            ("・\n- 要点A\n気づき: 無し。", ("要点A",), None),
            ("* 要点A\n気づき: 特になし", ("要点A",), None),
        ],
    )
    def test_IT_057_003_要点の行と気づきを切り出す(
        self, answered: str, gists: tuple[str, ...], noticing: str | None
    ) -> None:
        call = _Answering(answered)
        episodes = (_episode(1, "やっと通った"), _episode(2, "三時間かかった", 1))

        summarized = ClaudeCodeSummarizing(call).summarize(
            Identity(1, "わたしはそらです。"), episodes
        )

        assert summarized == Summarized(gists, noticing)
        assert call.prefaces == ["わたしはそらです。"]
        assert "相手「やっと通った」／あなた「はい」" in call.spoken[0]

    @pytest.mark.parametrize(
        "answered", ["気づき: 無し", "   ", "要点を箇条書きにしませんでした", "・\n-"]
    )
    def test_IT_057_003_要点が無ければ断る(self, answered: str) -> None:
        with pytest.raises(CannotDream):
            _ = ClaudeCodeSummarizing(_Answering(answered)).summarize(
                Identity(1, "x"), (_episode(1),)
            )


class TestIT057004:
    """要点が思い出しと声の前置きに流れる。"""

    def test_IT_057_004_最新の夢の要点が思い出しに添い前置きに並ぶ(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        dream = memories.record_dream(SORA.id, AT, AT, AT, 1, 1, "急ぐと見落とす")
        for text in ("原因はタイポ", "三時間かかった"):
            memories.record_gist(SORA.id, dream.id, Gist(text, AT, (1,)))

        recollected = Conversation(memories, PAIRS, lambda: AT).recall(SORA.id, "おはよう")
        call = _Answering("はい。")
        _ = ClaudeCodeVoice(call).speak(recollected, "おはよう")

        assert recollected.dream is not None
        assert [one.text for one in recollected.dream.gists] == ["原因はタイポ", "三時間かかった"]
        expected = (
            "残した要点です。\n- 原因はタイポ\n- 三時間かかった\n"
            + "そのとき気づいたこと: 急ぐと見落とす"
        )
        assert expected in call.prefaces[0]

    def test_IT_057_004_夢が無ければ添わない(self) -> None:
        call = _Answering("はい。")
        recollection = Recollection(
            Identity(1, "x"), (), (), State(Mood(0.0), Character(0.0)), None
        )

        _ = ClaudeCodeVoice(call).speak(recollection, "や")

        assert "要点" not in call.prefaces[0]

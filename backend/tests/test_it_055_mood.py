"""INC-055 の結合テスト。動きの積み方、現在値の求め方、声の切り出し、一往復の受け渡し。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.adapter.voice import ClaudeCodeVoice
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import (
    MOOD_HALF_LIFE,
    Dweller,
    Identity,
    Memories,
    Mood,
    Moved,
    Recollection,
    Shift,
)
from yadori.usecase.conversation import Conversation, Turn

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@final
class _Clock:
    def __init__(self) -> None:
        self.at: datetime = AT

    def __call__(self) -> datetime:
        return self.at


@final
class _Answering:
    """道具の口の代わり。決めた文を返し、渡された前置きを覚える。"""

    def __init__(self, answered: str) -> None:
        self._answered: str = answered
        self.prefaces: list[str] = []
        self.spoken: list[str] = []

    def ask(self, preface: str, spoken: str) -> str:
        self.prefaces.append(preface)
        self.spoken.append(spoken)
        return self._answered


@final
class _Fixed:
    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        del recollection
        return Spoken(f"{utterance} ですね", Moved(0.4, "うれしい"))


def _settled(memories: Memories) -> None:
    memories.settle(SORA)
    _ = memories.write_identity(SORA.id, "わたしはそらです。")


class TestIT055001:
    """動きが積まれ、現在値が計算で決まり、保存の実装で変わらない。"""

    def test_IT_055_001_二つの保存で同じ動きと同じ値になり上書きされない(
        self, tmp_path: Path
    ) -> None:
        stores: list[Memories] = [InMemoryMemories(), SqliteMemories(tmp_path / "m.sqlite")]
        moves = [
            Shift(AT, -0.3, "一緒に困っている", None),
            Shift(AT + timedelta(minutes=5), 0.5, "ほっとした", None),
            Shift(AT + timedelta(minutes=10), 0.0, "動きなし", None),
        ]

        for store in stores:
            _settled(store)
            for shift in moves:
                store.record_shift(SORA.id, shift)

        kept = [store.shifts(SORA.id) for store in stores]
        assert kept[0] == kept[1] == tuple(moves)
        now = AT + timedelta(minutes=10)
        values = [Mood.from_shifts(one, now, MOOD_HALF_LIFE).value for one in kept]
        assert values[0] == pytest.approx(values[1])
        assert values[0] == pytest.approx(-0.3 * 0.5 ** (600 / 21600) + 0.5 * 0.5 ** (300 / 21600))

    def test_IT_055_001_動きが無ければ0で和が越えれば収まる(self) -> None:
        assert Mood.from_shifts((), AT, MOOD_HALF_LIFE).value == 0.0
        piled = [Shift(AT, 0.8, "a", None), Shift(AT, 0.8, "b", None)]
        assert Mood.from_shifts(piled, AT, MOOD_HALF_LIFE).value == 1.0
        sunk = [Shift(AT, -0.8, "a", None), Shift(AT, -0.8, "b", None)]
        assert Mood.from_shifts(sunk, AT, MOOD_HALF_LIFE).value == -1.0
        assert Mood(0.0).described == "落ち着いている"
        assert Mood(-0.3).described == "沈んでいる" and Mood(0.3).described == "明るい"


class TestIT055002:
    """覚えるが動きを積み、思い出すが気持ちを添える。"""

    def test_IT_055_002_動き付きで覚えたときだけ積まれ思い出しに気持ちが添う(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        clock = _Clock()
        conversation = Conversation(memories, CharacterPairs(), clock)

        moved = conversation.remember(SORA.id, "やっと通った", "よかった", Moved(0.5, "ほっとした"))
        _ = conversation.remember(SORA.id, "次の課題です", "はい")
        recollected = conversation.recall(SORA.id, "どう？")

        shifts = memories.shifts(SORA.id)
        assert len(shifts) == 1
        assert shifts[0].episode_id == moved.id and shifts[0].cause == "ほっとした"
        assert recollected.mood.value == pytest.approx(0.5)
        assert isinstance(recollected.identity, Identity)


class TestIT055003:
    """声の切り出しと、一往復の受け渡し。"""

    def _recollection(self, mood: float = 0.0) -> Recollection:
        return Recollection(Identity(1, "わたしはそらです。"), (), (), Mood(mood))

    @pytest.mark.parametrize(
        ("answered", "reply", "delta", "cause"),
        [
            (
                "それはしんどいですね。\n【気持ち】-0.3 一緒に困っている",
                "それはしんどいですね。",
                -0.3,
                "一緒に困っている",
            ),
            ("よかった！\n\n【気持ち】 +0.5 ほっとした\n", "よかった！", 0.5, "ほっとした"),
            ("そうですね。\n【気持ち】−0.2 少し残念", "そうですね。", -0.2, "少し残念"),
            ("動きの行が無い返事です。", "動きの行が無い返事です。", 0.0, "動きなし"),
            ("範囲外です。\n【気持ち】3 うれしすぎる", "範囲外です。", 1.0, "うれしすぎる"),
            ("一言が無い。\n【気持ち】0.1", "一言が無い。", 0.1, "理由なし"),
            ("全角です。\n【気持ち】－０.３ 残念", "全角です。", -0.3, "残念"),
            ("数が無い。\n【気持ち】", "数が無い。", 0.0, "動きなし"),
            ("区切りが違う。\n【気持ち】0,5 うれしい", "区切りが違う。", 0.0, "動きなし"),
            (
                "印が二つ。\n【気持ち】-0.3 沈む\nでも大丈夫。\n【気持ち】+0.2 持ち直した",
                "印が二つ。\nでも大丈夫。",
                0.2,
                "持ち直した",
            ),
        ],
    )
    def test_IT_055_003_末尾の一行を切り出し無ければ動きなし(
        self, answered: str, reply: str, delta: float, cause: str
    ) -> None:
        call = _Answering(answered)

        spoken = ClaudeCodeVoice(call).speak(self._recollection(-0.4), "どう？")

        assert spoken.reply == reply
        assert spoken.moved == Moved(delta, cause)
        assert "いまのあなたの気持ちは「沈んでいる」（-0.40" in call.prefaces[0]
        assert "【気持ち】" in call.spoken[0]

    def test_IT_055_003_印の行だけの返事は応対を作れなかったとして断る(self) -> None:
        with pytest.raises(CannotSpeak):
            _ = ClaudeCodeVoice(_Answering("【気持ち】+0.5 うれしい")).speak(
                self._recollection(), "どう？"
            )
        with pytest.raises(ValueError):
            _ = Moved(99.0, "壊れた声")

    def test_IT_055_003_一往復の結果に動きと動いた後の気持ちが載る(self) -> None:
        memories = InMemoryMemories()
        _settled(memories)
        turn = Turn(Conversation(memories, CharacterPairs(), _Clock()), _Fixed())

        response = turn.respond_to(SORA.id, "やっと通った")

        assert response.moved == Moved(0.4, "うれしい")
        assert response.mood.value == pytest.approx(0.4)
        assert response.recollection.mood.value == 0.0
        assert memories.shifts(SORA.id)[0].episode_id == response.episode.id

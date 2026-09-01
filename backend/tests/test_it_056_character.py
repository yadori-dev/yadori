"""INC-056 の結合テスト。同じ動きから半減期と効き方だけ違う値が出て、時点で切れ、原文が添う。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.memory import (
    CHARACTER_HALF_LIFE,
    CHARACTER_WEIGHT,
    MOOD_HALF_LIFE,
    Character,
    Dweller,
    Fading,
    Memories,
    Mood,
    Shift,
    State,
)

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class TestIT056001:
    """同じ動きから半減期と効き方だけ違う値が出て、保存の実装で変わらない。"""

    def test_IT_056_001_二つの保存で同じ値になり性格は小さく長く残る(self, tmp_path: Path) -> None:
        stores: list[Memories] = [InMemoryMemories(), SqliteMemories(tmp_path / "m.sqlite")]
        moves = [
            Shift(AT, 0.5, "ほっとした", None),
            Shift(AT + timedelta(hours=1), 0.3, "うれしい", None),
        ]
        for store in stores:
            store.settle(SORA)
            for shift in moves:
                store.record_shift(SORA.id, shift)

        now = AT + timedelta(hours=7)
        states = [State.from_shifts(store.shifts(SORA.id), now) for store in stores]

        assert states[0] == states[1]
        assert states[0].mood.value == pytest.approx(Fading(MOOD_HALF_LIFE).sum(moves, now))
        assert states[0].character.value == pytest.approx(
            Fading(CHARACTER_HALF_LIFE, CHARACTER_WEIGHT).sum(moves, now)
        )
        # 気持ちは 7 時間で半分近くまで薄れるが、性格は十分の一のまま ほぼ残る。
        assert states[0].mood.value < 0.8 * 0.6
        assert states[0].character.value == pytest.approx(0.08, abs=0.001)

    def test_IT_056_001_係数と半減期だけが違い和は収まる(self) -> None:
        piled = [Shift(AT, 1.0, "a", None)] * 20

        assert Character.from_shifts(piled, AT).value == 1.0
        assert Mood.from_shifts(piled, AT).value == 1.0
        assert Character.from_shifts([Shift(AT, 0.4, "a", None)], AT).value == pytest.approx(0.04)
        assert (
            Character.from_shifts((), AT).value == 0.0
            and Character(0.0).described == "落ち着いている"
        )


class TestIT056002:
    """時点で切れ、原文が添う。"""

    def test_IT_056_002_保存先から往復の原文を引ける(self, tmp_path: Path) -> None:
        for store in (InMemoryMemories(), SqliteMemories(tmp_path / "m.sqlite")):
            store.settle(SORA)
            _ = store.write_identity(SORA.id, "わたしはそらです。")
            kept = store.write_episode(SORA.id, "やっと通った", "よかった", 1, AT)

            found = store.episode(kept.id)
            assert found is not None and found.utterance == "やっと通った"
            assert store.episode(kept.id + 100) is None
            if isinstance(store, SqliteMemories):
                store.close()

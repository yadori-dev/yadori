"""測るときに扱うもの。

言葉は docs/150_system/用語集.md を正典とする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True)
class Exchange:
    """評価セットで、あらかじめ覚えさせる一往復。名前で件から指す。"""

    name: str
    utterance: str
    reply: str


@final
@dataclass(frozen=True)
class Case:
    """一つの発話と、そのとき出るべきやりとり・出てはいけないやりとり。"""

    name: str
    utterance: str
    expected: tuple[str, ...]
    forbidden: tuple[str, ...]


@final
@dataclass(frozen=True)
class RecallEval:
    """評価セット。覚えさせるやりとりと、測る件を持つ。"""

    within: int
    exchanges: tuple[Exchange, ...]
    cases: tuple[Case, ...]


@final
@dataclass(frozen=True)
class Ranked:
    """あるやりとりが、探した記憶の何番目に出たか。

    出なかった場合は順位も近さも持たない。
    """

    name: str
    rank: int | None
    relevance: float | None

    def within(self, limit: int) -> bool:
        return self.rank is not None and self.rank <= limit


@final
@dataclass(frozen=True)
class Outcome:
    """一件を測った結果。

    期待するやりとりが直近として渡っていた件は測れない。意味で探す側に現れ
    ないためである。満たさなかった件と混ぜると、直近の往復数を狭めただけで
    良くなったように見える。
    """

    case: str
    expected: tuple[Ranked, ...]
    forbidden: tuple[Ranked, ...]
    in_recent: tuple[str, ...]

    @property
    def measurable(self) -> bool:
        return not self.in_recent

    def met(self, within: int) -> bool:
        """期待したやりとりのどれかが順位に入り、出てはいけないものが出ていない。

        期待を複数書けるのは、正しいと言えるやりとりが複数あるためである。
        すべて出ることを求めると、正しい別の候補を出したときに外れになる。
        """
        return (
            self.measurable
            and (not self.expected or any(one.within(within) for one in self.expected))
            and not any(one.rank is not None for one in self.forbidden)
        )


@final
@dataclass(frozen=True)
class Measurement:
    """全件の結果。要約はここから求め、別に持たない。"""

    within: int
    outcomes: tuple[Outcome, ...]

    @property
    def total(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.measurable)

    @property
    def unmeasurable(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.measurable)

    @property
    def met(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.met(self.within))

    @property
    def intruded(self) -> int:
        """出てはいけないやりとりが出た件の数。"""
        return sum(
            1 for outcome in self.outcomes if any(one.rank is not None for one in outcome.forbidden)
        )


@final
@dataclass(frozen=True)
class Shifted:
    """一件が、二つの測定の間でどう動いたか。"""

    case: str
    before: Outcome
    after: Outcome


@final
@dataclass(frozen=True)
class Difference:
    """二つの測定の差。変わらなかった件は持たない。"""

    better: tuple[Shifted, ...]
    worse: tuple[Shifted, ...]

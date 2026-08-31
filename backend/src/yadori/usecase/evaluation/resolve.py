"""判定の組を、測れる評価セットの形へ解く部品。

新しく作る手順も追記する手順も同じ部品を通す。規則が二本あると片方だけ直る。
並びの要素は一つの形（発話、返事、既にある名前があればその名前）で、前回のやりとりは
名前付き、新しい発話は名前無しで並ぶ。名前は前回の最後の番号から続け、下書きの中の
名前からは求めない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import final

from yadori.domain.evaluation import (
    Added,
    CannotDraft,
    CannotMeasure,
    Case,
    Exchange,
    Overlap,
    Pair,
    RecallEval,
)
from yadori.domain.memory import HowToRecall

# リポジトリの評価セットと同じ。何位までに入れば満たしたとするか。
WITHIN = 3


@final
@dataclass(frozen=True)
class Where:
    """記録の中の居場所。作業場所と時刻は必ず一緒にある。"""

    workspace: str
    at: datetime


@final
@dataclass(frozen=True)
class Placed:
    """解く並びの一つ。前回のやりとりは名前付き、新しい発話は名前無し。

    作業場所と時刻は候補を引く記憶へ入れる順を決めるためで、前回のやりとりは記録の
    読み直しから引く。記録に見つからない前回のやりとりは持たず、記憶に入らない。
    """

    name: str | None
    utterance: str
    reply: str
    where: Where | None

    @property
    def is_new(self) -> bool:
        return self.name is None


@final
@dataclass(frozen=True)
class Previous:
    """前回の下書き。新しく作るときは空で、番号は 0 から始まる。"""

    exchanges: tuple[Exchange, ...]
    cases: tuple[Case, ...]
    last_exchange: int
    last_case: int

    @property
    def utterances(self) -> frozenset[str]:
        return frozenset(one.utterance for one in self.exchanges) | frozenset(
            one.utterance for one in self.cases
        )


EMPTY_PREVIOUS = Previous(exchanges=(), cases=(), last_exchange=0, last_case=0)


@final
@dataclass(frozen=True)
class Resolved:
    """組を解いた結果。合わせた評価セットと、今回足した分と、使った名前の最後の番号。

    番号は下書きの中の名前からは求めない。人が名前を変えても消しても使い回さない。
    """

    recall_eval: RecallEval
    added: Added
    last_exchange: int
    last_case: int


@final
class Resolving:
    """組を解く。直近の往復数は思い出し方から取り、測るときと同じ窓で数える。"""

    def __init__(self, how: HowToRecall) -> None:
        self._how: HowToRecall = how
        self._overlap: Overlap = Overlap()

    def resolve(
        self, previous: Previous, placed: Sequence[Placed], pairs: Sequence[Pair]
    ) -> Resolved:
        """判定の結果を、測れる評価セットの形へ解く。

        - 同じ後の発話の組は、期待を複数持つ一問にまとめる
        - 問と期待の両方になる発話は期待に残し、問にしない
        - 問を外した後に残る並びの末尾の直近に入る期待は外す（数える並びは戻す前のもの）
        - 期待が一つも残らない発話は問にせず、覚えさせる側に戻す
        - 名前は前回の最後の番号の次から付け、合わせた評価セットで指す先を確かめる
        """
        expected_of = self._kept_as_expected(self._merged(pairs))
        exchange_indexes = [index for index in range(len(placed)) if index not in expected_of]
        recent = (
            set(exchange_indexes[-self._how.recent_turns :])
            if self._how.recent_turns
            else set[int]()
        )
        case_of: dict[int, list[int]] = {}
        for later in sorted(expected_of):
            earliers = [earlier for earlier in expected_of[later] if earlier not in recent]
            if earliers:
                case_of[later] = earliers
        exchange_indexes = [index for index in range(len(placed)) if index not in case_of]
        names = self._named(placed, exchange_indexes, previous.last_exchange)
        new_exchanges = tuple(
            Exchange(
                name=names[index], utterance=placed[index].utterance, reply=placed[index].reply
            )
            for index in exchange_indexes
            if placed[index].is_new
        )
        new_cases = tuple(
            self._case(placed, later, earliers, names, previous.last_case + number)
            for number, (later, earliers) in enumerate(sorted(case_of.items()), start=1)
        )
        recall_eval = RecallEval(
            within=WITHIN,
            exchanges=previous.exchanges + new_exchanges,
            cases=previous.cases + new_cases,
        )
        try:
            recall_eval.verify_pointing()
        except CannotMeasure as broken:
            raise CannotDraft(f"下書きが測れる形になりません: {broken}") from broken
        return Resolved(
            recall_eval=recall_eval,
            added=Added(exchanges=new_exchanges, cases=new_cases),
            last_exchange=previous.last_exchange + len(new_exchanges),
            last_case=previous.last_case + len(new_cases),
        )

    def _named(
        self, placed: Sequence[Placed], exchange_indexes: Sequence[int], last_exchange: int
    ) -> dict[int, str]:
        names: dict[int, str] = {}
        number = last_exchange
        for index in exchange_indexes:
            name = placed[index].name
            if name is None:
                number += 1
                name = f"e{number:03d}"
            names[index] = name
        return names

    def _merged(self, pairs: Sequence[Pair]) -> dict[int, list[int]]:
        merged: dict[int, list[int]] = {}
        for pair in pairs:
            earliers = merged.setdefault(pair.later, [])
            if pair.earlier not in earliers:
                earliers.append(pair.earlier)
        return merged

    def _kept_as_expected(self, expected_of: dict[int, list[int]]) -> dict[int, list[int]]:
        pointed = {earlier for earliers in expected_of.values() for earlier in earliers}
        return {later: earliers for later, earliers in expected_of.items() if later not in pointed}

    def _case(
        self,
        placed: Sequence[Placed],
        later: int,
        earliers: Sequence[int],
        names: dict[int, str],
        number: int,
    ) -> Case:
        utterance = placed[later].utterance
        return Case(
            name=f"c{number:03d}",
            utterance=utterance,
            expected=tuple(names[earlier] for earlier in earliers),
            forbidden=(),
            confirmed=False,
            overlap=tuple(
                (names[earlier], self._overlap.between(utterance, placed[earlier].utterance))
                for earlier in earliers
            ),
        )

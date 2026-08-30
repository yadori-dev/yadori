"""評価セットを測る。

測るたびに使い捨ての記憶へやりとりを入れ直すため、同じ評価セットと同じ
条件なら何度測っても同じ結果になる。持ち主の記憶は開かない。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import final

from yadori.domain.evaluation import (
    CannotMeasure,
    Case,
    Measurement,
    Outcome,
    Ranked,
    RecallEval,
)
from yadori.domain.memory import Dweller, Embeddings, Found, HowToRecall, Memories
from yadori.usecase.conversation import Conversation

MEASURED = Dweller(id="measured", owner="測るためだけの持ち主", name="測り手", nickname="測り手")
NAME_DECLARED = "測るためだけの名乗り。応対は作らない。"


@final
class Measuring:
    def __init__(
        self,
        recall_eval: RecallEval,
        fresh_memories: Callable[[], Memories],
        embeddings: Embeddings,
    ) -> None:
        self._eval: RecallEval = recall_eval
        self._fresh_memories: Callable[[], Memories] = fresh_memories
        self._embeddings: Embeddings = embeddings

    def at(self, how: HowToRecall) -> Measurement:
        """その条件で全件を測る。

        - 指す先が揃っているか確かめる
        - 使い捨ての記憶へやりとりを入れる
        - 索引が揃っているか確かめる
        - 件ごとに思い出して順位を取る
        """
        self._check_pointing()
        kept = self._filled()
        self._check_indexed(kept)
        conversation = Conversation(kept, self._embeddings, self._clock(), how)
        return Measurement(
            within=self._eval.within,
            outcomes=tuple(self._outcome(conversation, case) for case in self._eval.cases),
        )

    def _check_pointing(self) -> None:
        """件が指すやりとりが、評価セットの中に在ることを確かめる。"""
        known = {exchange.name for exchange in self._eval.exchanges}
        for case in self._eval.cases:
            unknown = sorted((set(case.expected) | set(case.forbidden)) - known)
            if unknown:
                raise CannotMeasure(f"件「{case.name}」が無いやりとりを指している: {unknown}")
            both = sorted(set(case.expected) & set(case.forbidden))
            if both:
                raise CannotMeasure(
                    f"件「{case.name}」が同じやりとりを期待と禁止に指している: {both}"
                )

    def _filled(self) -> Memories:
        """使い捨ての記憶へ、評価セットのやりとりを順に入れる。"""
        kept = self._fresh_memories()
        kept.settle(MEASURED)
        _ = kept.write_identity(MEASURED.id, NAME_DECLARED)
        conversation = Conversation(kept, self._embeddings, self._clock())
        for exchange in self._eval.exchanges:
            _ = conversation.remember(MEASURED.id, exchange.utterance, exchange.reply)
        return kept

    def _check_indexed(self, kept: Memories) -> None:
        """索引を持たないやりとりが無いことを確かめる。"""
        missing = kept.episodes_without_index(MEASURED.id)
        if missing:
            raise CannotMeasure(f"索引を持たないやりとりがある: {len(missing)}件")

    def _outcome(self, conversation: Conversation, case: Case) -> Outcome:
        """一件を測る。探した記憶だけを見る。直近は数えない。"""
        found = conversation.recall(MEASURED.id, case.utterance).found
        return Outcome(
            case=case.name,
            expected=tuple(self._ranked(name, found) for name in case.expected),
            forbidden=tuple(self._ranked(name, found) for name in case.forbidden),
        )

    def _ranked(self, name: str, found: tuple[Found, ...]) -> Ranked:
        """あるやりとりが、探した記憶の何番目に出たかを取る。"""
        utterance = self._utterance_of(name)
        for place, one in enumerate(found, start=1):
            if one.episode.utterance == utterance:
                return Ranked(name=name, rank=place, relevance=one.relevance)
        return Ranked(name=name, rank=None, relevance=None)

    def _utterance_of(self, name: str) -> str:
        for exchange in self._eval.exchanges:
            if exchange.name == name:
                return exchange.utterance
        raise CannotMeasure(f"やりとり「{name}」が評価セットに無い")

    def _clock(self) -> _Ticking:
        """測るたびに同じ時刻から進める。結果を時刻に左右させない。"""
        return _Ticking()


@final
class _Ticking:
    def __init__(self) -> None:
        self._at: datetime = datetime(2000, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at

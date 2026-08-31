"""評価セットを測って、結果を書く。

条件を変えて二度測ると、件ごとの差を良くなった件と悪くなった件に分けて書く。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.embedding import Multilingual
from yadori.adapter.evaluation import EvalFile
from yadori.adapter.store import InMemoryMemories
from yadori.domain.evaluation import CannotMeasure, Difference, Measurement, Outcome
from yadori.domain.memory import Embeddings, EmbeddingsUnavailable, HowToRecall
from yadori.usecase.evaluation import Comparing, Measuring

DEFAULT_EVAL = Path("evals/recall.toml")


@final
class Measure:
    """思い出す条件を測る。"""

    def __init__(
        self,
        eval_path: Path | None = None,
        baseline: HowToRecall | None = None,
        changed: HowToRecall | None = None,
        embeddings: Embeddings | Sequence[Embeddings] | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._eval_path: Path = eval_path or DEFAULT_EVAL
        self._baseline: HowToRecall = baseline or HowToRecall()
        self._changed: HowToRecall | None = changed
        self._embeddings: Embeddings | Sequence[Embeddings] = embeddings or Multilingual()
        self._writing: TextIO = writing or sys.stdout

    def run(self) -> int:
        """測って書く。

        - 評価セットを読む
        - 今の条件で測る
        - 変えた条件を渡されていれば、もう一度測って差を書く
        """
        try:
            recall_eval = EvalFile(self._eval_path).read()
            measuring = Measuring(recall_eval, InMemoryMemories, self._embeddings)
            now = measuring.at(self._baseline)
            self._write(now)
            if self._changed is not None:
                self._write_difference(measuring, now, self._changed)
        except (CannotMeasure, EmbeddingsUnavailable) as reason:
            print(f"測れません: {reason}", file=sys.stderr)
            return 1
        return 0

    def _named(self) -> str:
        if isinstance(self._embeddings, Sequence):
            return "＋".join(way.name for way in self._embeddings)
        return self._embeddings.name

    def _write(self, measurement: Measurement) -> None:
        """要約と、満たさなかった件を書く。要約は件ごとの結果から求める。"""
        self._say(f"埋め込み: {self._named()}")
        self._say(
            f"{measurement.total}件中 {measurement.met}件で"
            + f"期待したやりとりが上位{measurement.within}件に入った"
        )
        self._say(f"出てはいけないやりとりが出た件: {measurement.intruded}件")
        if measurement.unmeasurable:
            self._say(
                f"測れない件: {measurement.unmeasurable}件"
                + "（期待するやりとりが直近として渡っている）"
            )
        for outcome in measurement.outcomes:
            if not outcome.measurable:
                self._say(
                    f"  測れず: 「{outcome.case}」直近に入った: {'、'.join(outcome.in_recent)}"
                )
            elif not outcome.met(measurement.within):
                self._say(f"  満たさず: {self._detail(outcome)}")

    def _write_difference(
        self, measuring: Measuring, before: Measurement, changed: HowToRecall
    ) -> None:
        after = measuring.at(changed)
        self._say("")
        self._say(
            f"直近{changed.recent_turns}往復・上限{changed.found_limit}件・"
            + f"下限{changed.relevance_floor}にして測り直した"
        )
        self._write(after)
        self._say("")
        self._write_shifts(Comparing(before, after).difference())

    def _write_shifts(self, difference: Difference) -> None:
        if not difference.better and not difference.worse:
            self._say("満たし方が変わった件はない")
            return
        for shifted in difference.better:
            self._say(f"良くなった: {shifted.case}  {self._detail(shifted.after)}")
        for shifted in difference.worse:
            self._say(f"悪くなった: {shifted.case}  {self._detail(shifted.after)}")

    def _detail(self, outcome: Outcome) -> str:
        expected = (
            "、".join(
                f"{one.name} {'出ず' if one.rank is None else str(one.rank) + '位'}"
                for one in outcome.expected
            )
            or "期待なし"
        )
        intruded = "、".join(
            f"{one.name} {one.rank}位" for one in outcome.forbidden if one.rank is not None
        )
        return f"「{outcome.case}」期待: {expected}" + (f" ／ 混入: {intruded}" if intruded else "")

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")

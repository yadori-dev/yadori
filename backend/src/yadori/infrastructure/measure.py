"""評価セットを測って、結果を書く。

条件を変えて二度測ると、問ごとの差を良くなった問と悪くなった問に分けて書く。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.embedding import Weighing
from yadori.adapter.evaluation import EvalFile
from yadori.adapter.store import InMemoryMemories
from yadori.domain.evaluation import CannotMeasure, Difference, Measurement, Outcome
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall
from yadori.usecase.evaluation import Comparing, Measuring

DEFAULT_EVAL = Path("evals/recall.toml")


@final
class Measure:
    """思い出す条件を測る。埋め込みは重さを量る包みで受け取り、自分では組まない。"""

    def __init__(
        self,
        embeddings: Weighing | Sequence[Weighing],
        eval_path: Path | None = None,
        baseline: HowToRecall | None = None,
        changed: HowToRecall | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._eval_path: Path = eval_path or DEFAULT_EVAL
        self._baseline: HowToRecall = baseline or HowToRecall()
        self._changed: HowToRecall | None = changed
        self._ways: tuple[Weighing, ...] = (
            tuple(embeddings) if isinstance(embeddings, Sequence) else (embeddings,)
        )
        self._writing: TextIO = writing or sys.stdout

    def run(self) -> int:
        """測って書く。

        - 評価セットを読む
        - 今の条件で測る
        - 変えた条件を渡されていれば、もう一度測って差を書く
        """
        try:
            recall_eval = EvalFile(self._eval_path).read()
            measuring = Measuring(recall_eval, InMemoryMemories, self._ways)
            now = measuring.at(self._baseline)
            self._write(now, self._baseline)
            if self._changed is not None:
                self._write_difference(measuring, now, self._changed)
        except (CannotMeasure, EmbeddingsUnavailable) as reason:
            print(f"測れません: {reason}", file=sys.stderr)
            return 1
        return 0

    def _write(self, measurement: Measurement, how: HowToRecall) -> None:
        """埋め込みと重さ、条件、要約、満たさなかった問を書く。要約は問ごとの結果から求める。

        埋め込みの行は測った後に書く。重さは測った後にしか揃わない。下限の意味は
        埋め込みごとに違うため、どの下限で測ったかを毎回書く。書かないと、別の
        埋め込みを既定の下限で測った数を見比べてしまう。
        """
        for way in self._ways:
            origin = way.provenance
            self._say(f"埋め込み: {origin.described} 添え書き: {origin.prefixes_described}")
            self._say(way.weighed().described)
        self._say(self._conditions(how))
        self._say(
            f"{measurement.total}問中 {measurement.met}問で"
            + f"期待したやりとりが上位{measurement.within}件に入った"
        )
        self._say(f"出てはいけないやりとりが出た問: {measurement.intruded}問")
        if measurement.unmeasurable:
            self._say(
                f"測れない問: {measurement.unmeasurable}問"
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
        self._say("条件を変えて測り直した")
        self._write(after, changed)
        self._say("")
        self._write_shifts(Comparing(before, after).difference())

    def _write_shifts(self, difference: Difference) -> None:
        if not difference.better and not difference.worse:
            self._say("満たし方が変わった問はない")
            return
        for shifted in difference.better:
            self._say(f"良くなった: {shifted.case}  {self._detail(shifted.after)}")
        for shifted in difference.worse:
            self._say(f"悪くなった: {shifted.case}  {self._detail(shifted.after)}")

    def _conditions(self, how: HowToRecall) -> str:
        return (
            f"条件: 直近{how.recent_turns}往復・上限{how.found_limit}件・"
            + f"下限{how.relevance_floor}"
        )

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

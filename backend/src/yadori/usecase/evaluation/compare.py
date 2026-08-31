"""二つの測定を突き合わせる。

全体では良くなっても悪くなった問があれば、その問を名指しできるようにする。
"""

from __future__ import annotations

from typing import final

from yadori.domain.evaluation import CannotMeasure, Difference, Measurement, Outcome, Shifted


@final
class Comparing:
    def __init__(self, before: Measurement, after: Measurement) -> None:
        self._before: Measurement = before
        self._after: Measurement = after

    def difference(self) -> Difference:
        """問ごとの差を、良くなった問と悪くなった問に分ける。

        - 問を突き合わせる
        - 満たし方が変わった問だけを拾う
        """
        better: list[Shifted] = []
        worse: list[Shifted] = []
        for before in self._before.outcomes:
            after = self._same(before.case)
            if not (before.measurable and after.measurable):
                # 片方でも測れない問は、良し悪しを言えない。
                continue
            was = before.met(self._before.within)
            now = after.met(self._after.within)
            if was == now:
                continue
            (better if now else worse).append(Shifted(case=before.case, before=before, after=after))
        return Difference(better=tuple(better), worse=tuple(worse))

    def _same(self, case: str) -> Outcome:
        for outcome in self._after.outcomes:
            if outcome.case == case:
                return outcome
        raise CannotMeasure(f"二つの測定で問が揃っていない: {case}")

"""夢の中で残すものを選ぶ規則。

人の眠りでは、残る記憶は選ばれている（理論資料）。何を選ぶかの定説は無いため、
基準は仮置きの推論である。思い出された往復、気持ちが大きく動いた往復、同じ話題が
繰り返し出た往復のいずれかに当たれば選ぶ。AIモデルに判定させない（ADR-011、ADR-018）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yadori.domain.memory import Episode

# 気持ちの動きがこれ以上なら「大きく動いた」とみなす。測っていない仮置き。
MOVED_ENOUGH = 0.3


@dataclass(frozen=True)
class Candidate:
    """選ぶ材料。一往復と、その往復について手元で分かること。"""

    episode: Episode
    retrieved: bool
    shift: float
    repeated: bool

    @property
    def kept(self) -> bool:
        return self.retrieved or abs(self.shift) >= MOVED_ENOUGH or self.repeated


@dataclass(frozen=True)
class Keeping:
    """残すものを選ぶ。当たる往復を元の順のまま返す。"""

    def keep(self, candidates: Sequence[Candidate]) -> tuple[Episode, ...]:
        return tuple(one.episode for one in candidates if one.kept)

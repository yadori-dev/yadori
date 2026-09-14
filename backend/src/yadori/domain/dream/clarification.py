"""返答の補完。根拠を辿れる候補だけを文章を書く相手へ渡す。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from yadori.domain.memory import Clarification, Episode

REVISION = 1
CANDIDATE_LIMIT = 100
CONTEXT_LIMIT = 3
CONTEXT_LETTERS = 12000


class Explaining(Protocol):
    def explain(
        self, target: Episode, preceding: tuple[Episode, ...], at: datetime
    ) -> Clarification: ...

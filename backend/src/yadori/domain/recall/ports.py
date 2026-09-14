"""検索の一往復にだけ使える参照と、固定した本文。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class References(Protocol):
    @property
    def suggested(self) -> frozenset[int]: ...

    def refer(self, episode_id: int) -> str: ...

    def document(self, reference: str, make: Callable[[int], str]) -> tuple[str, int]: ...

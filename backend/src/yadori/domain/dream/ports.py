"""夢が外へ求めること。

要点と気づきの文章を書くのは対話する道具で、この層はその呼び方を知らない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from yadori.domain.memory import Episode, Identity


class CannotDream(Exception):
    """要点を書けなかった。夢の記録も要点も思い出した記録も残さない。"""


@dataclass(frozen=True)
class Summarized:
    """書いてもらったもの。話題ごとの要点（一件以上）と、離れた記憶が結びついたときだけの気づき。"""

    gists: tuple[str, ...]
    noticing: str | None


class Summarizing(Protocol):
    """選んだ往復から、要点の並びと気づきを書くもの。"""

    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized: ...

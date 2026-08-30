"""思い出す規則。

直近のやりとりは意味を見ずに常に渡す。それより前は意味の近さで探して足す。
「それ」「あれ」のように指す語だけの発話は意味で探しても何も出ないため、
直近を別の道で渡す。二つを一つの並びへ混ぜない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from yadori.domain.memory.model import Found, Recollection
from yadori.domain.memory.ports import Embeddings, Memories, NameNotDeclared


@dataclass(frozen=True)
class HowToRecall:
    """思い出し方の値。

    初期値であり、測って直す。`AC-002` と `AC-006` を選ぶ増分で扱う。
    """

    recent_turns: int = 6
    found_limit: int = 5
    relevance_floor: float = 0.3


def recollect(
    memories: Memories,
    embeddings: Embeddings,
    dweller_id: str,
    utterance: str,
    now: datetime,
    how: HowToRecall,
) -> Recollection:
    """話しかけられた文章から、渡すものを組み立てる。

    名乗りを持たない宿りは応対を作れないため、探す前に断る。
    """
    identity = memories.current_identity(dweller_id)
    if identity is None:
        raise NameNotDeclared(dweller_id)

    recent = memories.recent(dweller_id, how.recent_turns)

    # 直近で渡すものを探した記憶へ重ねない。同じ記憶が二度現れると、
    # 何が効いたのかを読めなくなる。
    hits = memories.search(
        dweller_id,
        embeddings.of(utterance),
        how.found_limit,
        how.relevance_floor,
        exclude=[episode.id for episode in recent],
    )
    found = tuple(
        Found(episode=episode, relevance=relevance, retrieval=memories.retrieval(episode.id))
        for episode, relevance in hits
    )

    # 思い出したことを記録する。思い出しやすさはここから求める。
    memories.record_retrieval([found_one.episode.id for found_one in found], now)

    return Recollection(identity=identity, recent=recent, found=found)

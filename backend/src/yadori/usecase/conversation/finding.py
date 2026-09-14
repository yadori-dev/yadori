"""原文と補完の索引から探し、同じ往復を一件にする。記録は増やさない。"""

from __future__ import annotations

from yadori.domain.dream.clarification import REVISION
from yadori.domain.memory import Embeddings, Episode, Found, HowToRecall, Memories


class Finding:
    def __init__(self, memories: Memories, how: HowToRecall) -> None:
        self._memories: Memories = memories
        self._how: HowToRecall = how

    def by(
        self, way: Embeddings, dweller_id: str, utterance: str, skip: list[int]
    ) -> tuple[Found, ...]:
        """一つの道で探す。"""
        vector = way.to_recall(utterance)
        hits = self._memories.search(
            dweller_id,
            way.name,
            vector,
            self._how.found_limit,
            self._how.relevance_floor,
            exclude=skip,
        )
        contextual = self._memories.search_clarifications(
            dweller_id,
            way.name,
            vector,
            self._how.found_limit,
            max(self._how.relevance_floor, self._how.clarification_floor),
            skip,
            REVISION,
        )
        best: dict[int, tuple[Episode, float]] = {}
        for episode, relevance in (*hits, *contextual):
            if episode.id not in best or relevance > best[episode.id][1]:
                best[episode.id] = (episode, relevance)
        merged = sorted(best.values(), key=lambda pair: (-pair[1], -pair[0].id))
        return tuple(
            self._with_retrieval(episode, relevance, way.name)
            for episode, relevance in merged[: self._how.found_limit]
        )

    def _with_retrieval(self, episode: Episode, relevance: float, way: str) -> Found:
        """近さと思い出した記録を、別の値として並べる。一つの点数へ混ぜない。"""
        record = self._memories.clarification(episode.id, REVISION)
        if record is not None and record.status != "clarified":
            record = None
        evidence = (
            tuple(self._memories.episode(source) for source in record.sources) if record else ()
        )
        if any(one is None for one in evidence):
            raise RuntimeError("補完の根拠が見つからない")
        return Found(
            episode=episode,
            relevance=relevance,
            retrieval=self._memories.retrieval(episode.id),
            way=way,
            clarification=record,
            evidence=tuple(one for one in evidence if one is not None),
        )

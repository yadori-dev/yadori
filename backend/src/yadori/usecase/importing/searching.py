"""同じ埋め込みの内側で候補を比較し、道ごとには交互に選ぶ。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import final

from yadori.domain.importing.model import ExternalFound
from yadori.domain.importing.ports import Archive
from yadori.domain.memory import Embeddings, Found, HowToRecall, Memories
from yadori.usecase.recall.finding import Finding

Hit = Found | ExternalFound


@final
class Searching:
    def __init__(self, memories: Memories, archive: Archive | None, how: HowToRecall) -> None:
        self._memories = memories
        self._archive = archive
        self._how = how

    def find(
        self, ways: Sequence[Embeddings], person: str, utterance: str, skip: list[int]
    ) -> tuple[Hit, ...]:
        by_way = [self._by(way, person, utterance, skip) for way in ways]
        result: list[Hit] = []
        seen: set[tuple[str, int]] = set()
        for position in range(self._how.found_limit):
            for hits in by_way:
                if position < len(hits):
                    hit = hits[position]
                    key = self.key(hit)
                    if key not in seen:
                        seen.add(key)
                        result.append(hit)
        return tuple(result[: self._how.found_limit])

    def _by(self, way: Embeddings, person: str, utterance: str, skip: list[int]) -> tuple[Hit, ...]:
        own = Finding(self._memories, self._how).by(way, person, utterance, skip)
        external = (
            ()
            if self._archive is None
            else self._archive.search(
                person,
                way.name,
                way.to_recall(utterance),
                self._how.relevance_floor,
                self._how.found_limit,
            )
        )
        return tuple(
            sorted(
                (*own, *external),
                key=lambda hit: (-hit.relevance, -self.key(hit)[1], self.key(hit)[0]),
            )[: self._how.found_limit]
        )

    @staticmethod
    def key(hit: Hit) -> tuple[str, int]:
        return (
            ("episode", hit.episode.id) if isinstance(hit, Found) else ("external", hit.record.id)
        )

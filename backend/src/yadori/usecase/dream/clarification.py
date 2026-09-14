"""通常の夢とは別に、短い返答の未処理を件数で区切って補う。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from yadori.domain.dream import CannotDream
from yadori.domain.dream.clarification import (
    CANDIDATE_LIMIT,
    CONTEXT_LETTERS,
    CONTEXT_LIMIT,
    REVISION,
    Explaining,
)
from yadori.domain.memory import Clarification, Embeddings, Episode, Memories


@dataclass(frozen=True)
class Clarified:
    records: tuple[Clarification, ...]
    remaining: bool


class Clarifying:
    def __init__(
        self,
        memories: Memories,
        embeddings: Embeddings,
        explaining: Explaining,
        now: Callable[[], datetime],
    ) -> None:
        self._memories: Memories = memories
        self._embeddings: Embeddings = embeddings
        self._explaining: Explaining = explaining
        self._now: Callable[[], datetime] = now
        self.records: list[Clarification] = []

    def run(self, dweller_id: str) -> Clarified:
        self.records.clear()
        for target in self._memories.unclarified(dweller_id, REVISION, CANDIDATE_LIMIT):
            record = self._explained(dweller_id, target)
            indexes = (
                ((self._embeddings.name, self._embeddings.to_remember(record.searchable(target))),)
                if record.status == "clarified"
                else ()
            )
            self._memories.keep_clarification(record, indexes)
            saved = self._memories.clarification(target.id, REVISION)
            if saved is None:
                raise RuntimeError("補完の確定結果が見つからない")
            self.records.append(saved)
        return self.result(dweller_id)

    def result(self, dweller_id: str) -> Clarified:
        return Clarified(
            tuple(self.records), bool(self._memories.unclarified(dweller_id, REVISION, 1))
        )

    def _explained(self, dweller_id: str, target: Episode) -> Clarification:
        if target.session_id is None:
            return self._deferred(target, "会話の所属が不明")
        preceding = self._preceding(dweller_id, target)
        if preceding is None:
            return self._deferred(target, "直接繋がる先行文脈が欠けている")
        if sum(len(one.utterance) + len(one.reply) for one in preceding) > CONTEXT_LETTERS:
            return self._deferred(target, "先行文脈が長さの上限を超えるため切り詰めず保留")
        record = self._explaining.explain(target, preceding, self._now())
        available = {one.id for one in preceding}
        if (
            record.episode_id != target.id
            or record.revision != REVISION
            or not set(record.sources) <= available
            or (
                record.status == "clarified"
                and (not preceding or preceding[-1].id not in record.sources)
            )
        ):
            raise CannotDream("補完の対象・規則・根拠が渡した先行文脈と一致しない")
        return record

    def _preceding(self, dweller_id: str, target: Episode) -> tuple[Episode, ...] | None:
        chain: list[Episode] = []
        current = target
        seen = {target.id}
        for _ in range(CONTEXT_LIMIT):
            if current.previous_source is None:
                break
            previous = self._memories.episode_from_source(dweller_id, current.previous_source)
            if previous is None or previous.session_id != target.session_id or previous.id in seen:
                return None
            if (previous.recalled_at or previous.happened_at) > (
                current.recalled_at or current.happened_at
            ):
                return None
            chain.append(previous)
            seen.add(previous.id)
            current = previous
        return tuple(reversed(chain))

    def _deferred(self, target: Episode, reason: str) -> Clarification:
        return Clarification(target.id, REVISION, "deferred", None, None, reason, self._now(), ())

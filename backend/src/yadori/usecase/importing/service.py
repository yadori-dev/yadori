"""取り込みは全体を確認してから、一往復ずつ原文と索引を確定する。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import final

from yadori.domain.importing.model import ExternalConversation, ImportFailed
from yadori.domain.importing.ports import Archive
from yadori.domain.memory import Embeddings


@dataclass(frozen=True)
class ImportPlan:
    added: tuple[ExternalConversation, ...]
    existing: int
    native: int


@final
class Importing:
    def __init__(self, archive: Archive, person: str) -> None:
        self._archive = archive
        self._person = person
        self.saved = 0

    def preview(self, conversations: Sequence[ExternalConversation]) -> ImportPlan:
        seen: dict[str, ExternalConversation] = {}
        added: list[ExternalConversation] = []
        existing = native = 0
        for record in conversations:
            if record.source in seen:
                if seen[record.source] != record:
                    raise ImportFailed(f"入力内の同じ出典に異なる原文があります: {record.source}")
                existing += 1
                continue
            seen[record.source] = record
            if self._archive.native_exists(self._person, record.native_source):
                native += 1
                continue
            kept = self._archive.existing(self._person, record.source)
            if kept is None:
                added.append(record)
            elif kept.conversation == record:
                existing += 1
            else:
                raise ImportFailed(
                    f"同じ出典の原文が変わっています。上書きしません: {record.source}"
                )
        return ImportPlan(tuple(added), existing, native)

    def apply(
        self,
        plan: ImportPlan,
        embeddings: Embeddings,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        self.saved = 0
        chosen = plan.added if limit is None else plan.added[:limit]
        for record in chosen:
            vector = embeddings.to_remember(record.utterance)
            self.saved += self._archive.keep(self._person, record, embeddings.name, vector)
            if progress:
                progress(self.saved, len(chosen))
        return self.saved

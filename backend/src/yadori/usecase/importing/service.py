"""取り込みは全体を確認してから、一往復ずつ原文と索引を確定する。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import final

from yadori.domain.importing.model import ExternalConversation, ImportFailed, LogContents
from yadori.domain.importing.ports import Archive
from yadori.domain.memory import Embeddings


@dataclass(frozen=True)
class ImportPlan:
    added: tuple[ExternalConversation, ...]
    existing: int
    native: int
    held: int = 0
    dependent: int = 0


@final
class Importing:
    def __init__(self, archive: Archive, person: str) -> None:
        self._archive = archive
        self._person = person
        self.saved = 0

    def preview_logs(self, logs: Sequence[LogContents]) -> ImportPlan:
        seen: dict[str, set[ExternalConversation]] = {}
        held: set[str] = set()
        originals: list[ExternalConversation] = []
        for log in logs:
            groups: dict[str, set[ExternalConversation]] = {}
            for record in (*log.conversations, *log.held):
                groups.setdefault(record.source, set()).add(record)
            for source, candidates in groups.items():
                if source in seen and seen[source] != candidates:
                    raise ImportFailed(f"複数ファイルで同じ出典の応対が一致しません: {source}")
                seen[source] = candidates
            held.update(record.source for record in log.held)
            originals.extend(log.conversations)
        ambiguous = len(held)
        following: dict[str, set[str]] = {}
        for one in originals:
            if one.previous is not None:
                previous = f"{one.provider}:{one.session}:{one.previous}"
                following.setdefault(previous, set()).add(one.source)
        pending = list(held)
        while pending:
            for source in following.get(pending.pop(), set()):
                if source not in held:
                    held.add(source)
                    pending.append(source)
        plan = self.preview([one for one in originals if one.source not in held])
        return replace(plan, held=len(held), dependent=len(held) - ambiguous)

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
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        self.saved = 0
        chosen = plan.added
        for record in chosen:
            vector = embeddings.to_remember(record.utterance)
            self.saved += self._archive.keep(self._person, record, embeddings.name, vector)
            if progress:
                progress(self.saved, len(chosen))
        return self.saved

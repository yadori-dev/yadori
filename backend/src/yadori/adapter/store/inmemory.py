"""手元の並びへ記憶を持つ。

保存の実装を差し替えても記憶の規則が変わらないことを確かめるために、
SQLite と同じ振る舞いを別の作りで持つ。プロセスが終わると消える。
"""

from __future__ import annotations

import copy
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import final

from yadori.adapter.embedding.characters import Closeness
from yadori.domain.memory import (
    Clarification,
    Dream,
    Dweller,
    Episode,
    Gist,
    Identity,
    Moved,
    RememberingConflict,
    Retrieval,
    Shift,
    Vector,
)


@dataclass
class _Kept:
    dwellers: dict[str, Dweller] = field(default_factory=dict)
    identities: dict[str, list[Identity]] = field(default_factory=dict)
    episodes: dict[int, tuple[str, Episode]] = field(default_factory=dict)
    index: dict[tuple[int, str], Vector] = field(default_factory=dict)
    retrievals: list[tuple[int, datetime]] = field(default_factory=list)
    shifts: list[tuple[str, Shift]] = field(default_factory=list)
    dreams: list[tuple[str, Dream]] = field(default_factory=list)
    gists: list[tuple[str, int, Gist]] = field(default_factory=list)
    clarifications: dict[tuple[int, int], Clarification] = field(default_factory=dict)
    clarification_index: dict[tuple[int, int, str], Vector] = field(default_factory=dict)
    next_id: int = 1


@final
class InMemoryMemories:
    def __init__(self) -> None:
        self._kept: _Kept = _Kept()
        self._closeness: Closeness = Closeness()

    def settle(self, dweller: Dweller) -> None:
        self._kept.dwellers[dweller.id] = dweller

    def dweller(self, dweller_id: str) -> Dweller | None:
        return self._kept.dwellers.get(dweller_id)

    def current_identity(self, dweller_id: str) -> Identity | None:
        versions = self._kept.identities.get(dweller_id)
        return versions[-1] if versions else None

    def write_identity(self, dweller_id: str, text: str) -> Identity:
        versions = self._kept.identities.setdefault(dweller_id, [])
        identity = Identity(version=len(versions) + 1, text=text)
        versions.append(identity)
        return identity

    def identity_at(self, dweller_id: str, version: int) -> Identity | None:
        for identity in self._kept.identities.get(dweller_id, []):
            if identity.version == version:
                return identity
        return None

    def _owned(self, dweller_id: str) -> list[Episode]:
        return [episode for owner, episode in self._kept.episodes.values() if owner == dweller_id]

    def recent(self, dweller_id: str, limit: int) -> tuple[Episode, ...]:
        owned = sorted(self._owned(dweller_id), key=lambda episode: episode.id)
        return tuple(owned[-limit:]) if limit else ()

    def search(
        self,
        dweller_id: str,
        model: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
    ) -> tuple[tuple[Episode, float], ...]:
        excluded = set(exclude)
        scored = [
            (episode, self._closeness.between(vector, self._kept.index[(episode.id, model)]))
            for episode in self._owned(dweller_id)
            if episode.id not in excluded and (episode.id, model) in self._kept.index
        ]
        scored = [pair for pair in scored if pair[1] >= floor]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return tuple(scored[:limit])

    def write_episode(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        identity_version: int,
        happened_at: datetime,
        recalled_at: datetime | None = None,
        source: str | None = None,
        session_id: str | None = None,
        previous_source: str | None = None,
    ) -> Episode:
        if source is not None:
            for owner, kept in self._kept.episodes.values():
                if owner == dweller_id and kept.source == source:
                    if (
                        kept.utterance != utterance
                        or kept.reply != reply
                        or kept.identity_version != identity_version
                        or kept.recalled_at != recalled_at
                        or kept.session_id != session_id
                        or kept.previous_source != previous_source
                    ):
                        raise RememberingConflict(f"同じ出典へ異なる一往復が届いた: {source}")
                    return kept
        episode = Episode(
            id=self._kept.next_id,
            utterance=utterance,
            reply=reply,
            identity_version=identity_version,
            happened_at=happened_at,
            recalled_at=recalled_at,
            source=source,
            session_id=session_id,
            previous_source=previous_source,
        )
        self._kept.episodes[episode.id] = (dweller_id, episode)
        self._kept.next_id += 1
        return episode

    def episode_from_source(self, dweller_id: str, source: str) -> Episode | None:
        return self._episode_from_source(dweller_id, source)

    def unclarified(self, dweller_id: str, revision: int, limit: int) -> tuple[Episode, ...]:
        return tuple(
            one
            for one in sorted(self._owned(dweller_id), key=lambda one: one.id)
            if len(one.utterance.strip()) <= 20
            and (one.id, revision) not in self._kept.clarifications
        )[:limit]

    def clarification(self, episode_id: int, revision: int) -> Clarification | None:
        return self._kept.clarifications.get((episode_id, revision))

    def keep_clarification(
        self, clarification: Clarification, indexes: Collection[tuple[str, Vector]]
    ) -> None:
        if clarification.status == "clarified" and not indexes:
            raise ValueError("説明と索引が揃うまで補完を確定できない")
        key = (clarification.episode_id, clarification.revision)
        if key in self._kept.clarifications:
            return
        before = copy.deepcopy(self._kept)
        try:
            self._kept.clarifications[key] = clarification
            for model, vector in indexes:
                self.write_clarification_index(*key, model, vector)
        except BaseException:
            self._kept = before
            raise

    def write_clarification_index(
        self, episode_id: int, revision: int, model: str, vector: Vector
    ) -> None:
        self._kept.clarification_index[(episode_id, revision, model)] = vector

    def clarifications_without_index(
        self, dweller_id: str, model: str, revision: int
    ) -> tuple[Clarification, ...]:
        owned = {one.id for one in self._owned(dweller_id)}
        return tuple(
            one
            for (target, version), one in self._kept.clarifications.items()
            if target in owned
            and version == revision
            and one.status == "clarified"
            and (target, version, model) not in self._kept.clarification_index
        )

    def search_clarifications(
        self,
        dweller_id: str,
        model: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
        revision: int,
    ) -> tuple[tuple[Episode, float], ...]:
        scored = [
            (
                one,
                self._closeness.between(
                    vector, self._kept.clarification_index[(one.id, revision, model)]
                ),
            )
            for one in self._owned(dweller_id)
            if one.id not in exclude and (one.id, revision, model) in self._kept.clarification_index
        ]
        near = [pair for pair in scored if pair[1] >= floor]
        near.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return tuple(near[:limit])

    def count_episodes(self, dweller_id: str) -> int:
        return len(self._owned(dweller_id))

    def keep_episode(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        identity_version: int,
        happened_at: datetime,
        recalled_at: datetime | None,
        source: str | None,
        indexes: Collection[tuple[str, Vector]],
        moved: Moved | None,
        session_id: str | None = None,
        previous_source: str | None = None,
    ) -> Episode:
        before = copy.deepcopy(self._kept)
        try:
            existing = self._episode_from_source(dweller_id, source)
            episode = self.write_episode(
                dweller_id,
                utterance,
                reply,
                identity_version,
                happened_at,
                recalled_at,
                source,
                session_id,
                previous_source,
            )
            if existing is not None:
                self._require_same_moved(dweller_id, episode.id, moved)
            elif moved is not None:
                self.record_shift(
                    dweller_id,
                    Shift(happened_at, moved.delta, moved.cause, episode.id),
                )
        except BaseException:
            self._kept = before
            raise
        for model, vector in indexes:
            self.write_index(episode.id, model, vector)
        return episode

    def _episode_from_source(self, dweller_id: str, source: str | None) -> Episode | None:
        if source is None:
            return None
        for owner, episode in self._kept.episodes.values():
            if owner == dweller_id and episode.source == source:
                return episode
        return None

    def _require_same_moved(self, dweller_id: str, episode_id: int, moved: Moved | None) -> None:
        kept = next(
            (
                shift
                for owner, shift in self._kept.shifts
                if owner == dweller_id and shift.episode_id == episode_id
            ),
            None,
        )
        if kept is None and moved is None:
            return
        if kept is None or moved is None or kept.delta != moved.delta or kept.cause != moved.cause:
            raise RememberingConflict(f"同じ一往復へ異なる気持ちの動きが届いた: {episode_id}")

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None:
        self._kept.index[(episode_id, model)] = vector

    def clear_index(self, dweller_id: str) -> None:
        owned = {one.id for one in self._owned(dweller_id)}
        self._kept.clarification_index = {
            key: value
            for key, value in self._kept.clarification_index.items()
            if key[0] not in owned
        }
        owned = {episode.id for episode in self._owned(dweller_id)}
        for key in [key for key in self._kept.index if key[0] in owned]:
            _ = self._kept.index.pop(key, None)

    def episodes_without_index(self, dweller_id: str, model: str) -> tuple[Episode, ...]:
        """いまの埋め込みのインデックスを持たない記憶。無視する側と同じ規則で決める。"""
        return tuple(
            episode
            for episode in sorted(self._owned(dweller_id), key=lambda one: one.id)
            if (episode.id, model) not in self._kept.index
        )

    def record_retrieval(self, episode_ids: Collection[int], at: datetime) -> None:
        self._kept.retrievals.extend((episode_id, at) for episode_id in episode_ids)

    def retrieval(self, episode_id: int) -> Retrieval:
        times = [at for kept_id, at in self._kept.retrievals if kept_id == episode_id]
        return Retrieval(count=len(times), last_at=max(times) if times else None)

    def record_shift(self, dweller_id: str, shift: Shift) -> None:
        if shift.episode_id is not None:
            for owner, kept in self._kept.shifts:
                if owner == dweller_id and kept.episode_id == shift.episode_id:
                    if kept.delta != shift.delta or kept.cause != shift.cause:
                        raise RememberingConflict(
                            f"同じ一往復へ異なる気持ちの動きが届いた: {shift.episode_id}"
                        )
                    return
        self._kept.shifts.append((dweller_id, shift))

    def shifts(self, dweller_id: str) -> tuple[Shift, ...]:
        return tuple(shift for owner, shift in self._kept.shifts if owner == dweller_id)

    def episode(self, episode_id: int) -> Episode | None:
        kept = self._kept.episodes.get(episode_id)
        return None if kept is None else kept[1]

    def episodes_after(self, dweller_id: str, at: datetime | None) -> tuple[Episode, ...]:
        owned = [one for one in self._owned(dweller_id) if at is None or one.happened_at > at]
        return tuple(sorted(owned, key=lambda episode: (episode.happened_at, episode.id)))

    def record_dream(
        self,
        dweller_id: str,
        at: datetime,
        read_from: datetime,
        read_to: datetime,
        count: int,
        kept: int,
        noticing: str | None,
    ) -> Dream:
        numbered = Dream(len(self._kept.dreams) + 1, at, read_from, read_to, count, kept, noticing)
        self._kept.dreams.append((dweller_id, numbered))
        return numbered

    def latest_dream(self, dweller_id: str) -> Dream | None:
        owned = [dream for owner, dream in self._kept.dreams if owner == dweller_id]
        return owned[-1] if owned else None

    def record_gist(self, dweller_id: str, dream_id: int, gist: Gist) -> None:
        self._kept.gists.append((dweller_id, dream_id, gist))

    def gists_of_dream(self, dream_id: int) -> tuple[Gist, ...]:
        return tuple(gist for _, owner_dream, gist in self._kept.gists if owner_dream == dream_id)

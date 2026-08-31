"""手元の並びへ記憶を持つ。

保存の実装を差し替えても記憶の規則が変わらないことを確かめるために、
SQLite と同じ振る舞いを別の作りで持つ。プロセスが終わると消える。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import final

from yadori.adapter.embedding.characters import Closeness
from yadori.domain.memory import Dweller, Episode, Identity, Retrieval, Vector


@dataclass
class _Kept:
    dwellers: dict[str, Dweller] = field(default_factory=dict)
    identities: dict[str, list[Identity]] = field(default_factory=dict)
    episodes: dict[int, tuple[str, Episode]] = field(default_factory=dict)
    index: dict[tuple[int, str], Vector] = field(default_factory=dict)
    retrievals: list[tuple[int, datetime]] = field(default_factory=list)
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
    ) -> Episode:
        episode = Episode(
            id=self._kept.next_id,
            utterance=utterance,
            reply=reply,
            identity_version=identity_version,
            happened_at=happened_at,
        )
        self._kept.episodes[episode.id] = (dweller_id, episode)
        self._kept.next_id += 1
        return episode

    def count_episodes(self, dweller_id: str) -> int:
        return len(self._owned(dweller_id))

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None:
        self._kept.index[(episode_id, model)] = vector

    def clear_index(self, dweller_id: str) -> None:
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

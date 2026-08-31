"""記憶の規則が外へ求めること。

実装は adapter が持つ。この層は保存の方法も模型の種類も知らない。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from yadori.domain.memory.model import Dweller, Episode, Identity, Retrieval, Vector


class NameNotDeclared(Exception):
    """名乗りを持たない宿りへ話しかけられた。

    応対を作らず、記憶も増やさない。
    """

    def __init__(self, dweller_id: str) -> None:
        super().__init__(f"宿り {dweller_id} は名乗りを持っていない")
        self.dweller_id: str = dweller_id


class EmbeddingsUnavailable(Exception):
    """埋め込みを使えない。

    思い出す手順へ入る前に断る。理由には何をすればよいかを含める。記憶は
    増やさない。
    """


class Memories(Protocol):
    """宿りの記憶の保存先。

    原文と索引は別々に扱う。索引は原文から作り直せる派生物である。
    """

    def settle(self, dweller: Dweller) -> None: ...

    def dweller(self, dweller_id: str) -> Dweller | None: ...

    def current_identity(self, dweller_id: str) -> Identity | None: ...

    def write_identity(self, dweller_id: str, text: str) -> Identity: ...

    def recent(self, dweller_id: str, limit: int) -> tuple[Episode, ...]: ...

    def search(
        self,
        dweller_id: str,
        model: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
    ) -> tuple[tuple[Episode, float], ...]: ...

    def write_episode(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        identity_version: int,
        happened_at: datetime,
    ) -> Episode: ...

    def count_episodes(self, dweller_id: str) -> int: ...

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None: ...

    def clear_index(self, dweller_id: str) -> None: ...

    def episodes_without_index(self, dweller_id: str, model: str) -> tuple[Episode, ...]: ...

    def record_retrieval(self, episode_ids: Collection[int], at: datetime) -> None: ...

    def retrieval(self, episode_id: int) -> Retrieval: ...


class Embeddings(Protocol):
    """文章を、意味の近いものどうしが近くなる数値の並びへ変える。

    どの模型のどの版で作ったかを索引へ残すため、名前を持つ。
    """

    @property
    def name(self) -> str: ...

    def of(self, text: str) -> Vector: ...

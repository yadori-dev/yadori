"""記憶として持つもの。

言葉は docs/150_system/用語集.md を正典とする。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

Vector = tuple[float, ...]


@dataclass(frozen=True)
class Dweller:
    """宿り。名前を持つ一人ぶんの存在で、記憶と状態はすべてここに属する。"""

    id: str
    owner: str
    name: str
    nickname: str


@dataclass(frozen=True)
class Identity:
    """名乗り。持ち主が書いた一続きの文章で、書き直すたびに版が増える。"""

    version: int
    text: str


@dataclass(frozen=True)
class Episode:
    """出来事の記憶。一度のやりとりを原文のまま持ち、消さない。"""

    id: int
    utterance: str
    reply: str
    identity_version: int
    happened_at: datetime


@dataclass(frozen=True)
class Retrieval:
    """思い出した記録。思い出しやすさはここから求める。

    求め方は未決のため、この増分では記録そのものを見せる。
    """

    count: int
    last_at: datetime | None


@dataclass(frozen=True)
class Found:
    """意味の近さで探し当てた記憶。

    近さと思い出した記録は別の値として持つ。一つの点数へ混ぜない。
    """

    episode: Episode
    relevance: float
    retrieval: Retrieval


@dataclass(frozen=True)
class HowToRecall:
    """思い出し方の値。

    初期値であり、根拠のある値ではない。`AC-002` と `AC-006` を選ぶ増分で
    測って直す。
    """

    recent_turns: int = 6
    found_limit: int = 5
    relevance_floor: float = 0.3


@dataclass(frozen=True)
class Recollection:
    """思い出したこと。

    直近のやりとりと探した記憶は別の道で来る。混ぜて一つの並びにしない。
    """

    identity: Identity
    recent: tuple[Episode, ...]
    found: tuple[Found, ...]

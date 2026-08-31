"""会話が外へ求めること。

実装は adapter が持つ。この層はAIモデルの種類も呼び方も知らない。
"""

from __future__ import annotations

from typing import Protocol

from yadori.domain.memory import Recollection


class CannotSpeak(Exception):
    """応対を作れなかった。

    やりとりを覚えたことにしない。
    """


class Voice(Protocol):
    """名乗りと思い出したことから、応対の文章を作るもの。

    宿り自身が話す場所でだけ使う。対話する道具では、その道具が返事を作る
    ため使わない。
    """

    def speak(self, recollection: Recollection, utterance: str) -> str: ...

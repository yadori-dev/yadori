"""端末で話す。

Discord ができるまでの間、手元で宿りと話すための場所。受け取って渡して
返すだけで、記憶の規則も応対の作り方も持たない。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import TextIO, final

from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Dweller, EmbeddingsUnavailable
from yadori.usecase.conversation import Turn


@final
class Terminal:
    def __init__(
        self,
        turn: Turn,
        dweller: Dweller,
        reading: TextIO | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._turn: Turn = turn
        self._dweller: Dweller = dweller
        self._reading: TextIO = reading or sys.stdin
        self._writing: TextIO = writing or sys.stdout

    def listen(self) -> None:
        """話しかけられるのを待ち、一往復ずつ通す。

        - 呼びかけを出す
        - 一行ずつ受け取る
        - 一往復を通して応対を書く
        """
        self._greet()
        for spoken_to in self._lines():
            self._respond(spoken_to)

    def _greet(self) -> None:
        self._say(f"（{self._dweller.name} が居ます。空行で終わります）")

    def _lines(self) -> Iterator[str]:
        while True:
            _ = self._writing.write("> ")
            self._writing.flush()
            line = self._reading.readline()
            if not line or not line.strip():
                return
            yield line.strip()

    def _respond(self, utterance: str) -> None:
        """一往復を通す。応対を作れなければ理由を書き、覚えさせない。

        埋め込みが使えないときも同じで、何を導入すればよいかをそのまま書く。
        導入の面倒はここでは見ない。
        """
        try:
            response = self._turn.respond_to(self._dweller.id, utterance)
        except (CannotSpeak, EmbeddingsUnavailable) as reason:
            self._say(f"（応対できませんでした: {reason}）")
            return
        self._say(f"{self._dweller.nickname}: {response.reply}")

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
        self._writing.flush()

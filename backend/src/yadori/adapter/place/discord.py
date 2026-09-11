"""Discord で話す。

宿り自身の言葉で話す出ていく先である（ADR-002）。受け取るのは持ち主本人からの直接の
会話だけで、サーバーの部屋には出ない（ADR-019）。

受け取る仕組みは口（`Gateway`）で受け、実物の繋ぎと差し替えられるようにする。届いた言葉は
値（`Heard`）にして、外の道具の型をここより内側へ出さない。答えるかどうかを決めるのは
この場所で、繋ぎは判断しない。考えている印を立てるのも、答えると決めた後のこの場所である。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, TextIO, final

from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Dweller, EmbeddingsUnavailable
from yadori.usecase.conversation import Turn

# Discord が一度に受け取れる長さ。超える返事は分けて送る。
LETTER_LIMIT = 2000
# 応対を作れないときに Discord へ返す文。理由の中身は送らない（ADR-019）。
CANNOT_RESPOND = "（いま応対を作れません。手元の記録を見てください）"


class CannotConnect(Exception):
    """Discord へ繋げない。トークンが違う、許可が足りない、など。"""


@dataclass(frozen=True)
class Heard:
    """届いた言葉。誰から、直接の会話か、何と言われたか。"""

    text: str
    author_id: int
    direct: bool
    from_myself: bool


Thinking = Callable[[], AbstractAsyncContextManager[object]]
"""考えている間そう見せる仕掛け。中に入っている間だけ相手に見える。"""

Answering = Callable[[Heard, Thinking], Coroutine[None, None, tuple[str, ...]]]
"""届いた言葉に対して返す文の並び。空なら答えない。"""


class Gateway(Protocol):
    """Discord との繋ぎ。届いた言葉と考えている印の出し方を渡し、返された文を送る。

    答えるかどうかも、いつ考えている印を出すかも判断しない。繋いで、渡して、送るだけである。
    """

    def listen(self, answering: Answering, greeting: str) -> None: ...


@final
class DiscordPlace:
    """Discord の出ていく先。持ち主の直接の会話にだけ、宿り自身の言葉で答える。"""

    def __init__(
        self,
        turn: Turn,
        dweller: Dweller,
        owner_id: int,
        gateway: Gateway,
        writing: TextIO | None = None,
    ) -> None:
        self._turn: Turn = turn
        self._dweller: Dweller = dweller
        self._owner_id: int = owner_id
        self._gateway: Gateway = gateway
        # 応対を作れない理由は手元にだけ書く。Discord へは送らない。
        self._writing: TextIO = writing or sys.stderr
        # 応対には対話する道具を起こす時間がかかる。重ならないよう一往復ずつ順に扱う。
        self._one_at_a_time: asyncio.Lock = asyncio.Lock()

    def listen(self) -> None:
        """話しかけられるのを待ち、一往復ずつ通す。"""
        self._gateway.listen(self._answer, self._greeting())

    def _greeting(self) -> str:
        return f"（{self._dweller.name} が Discord に居ます。終わるには Ctrl-C）"

    async def _answer(self, heard: Heard, thinking: Thinking) -> tuple[str, ...]:
        """届いた言葉に答える。答えない言葉には何も返さず、考えている印も立てない。

        考えている印は順番が回ってきてから立てる。待っている間ずっと出し続けると、
        待っている数だけ同じ印を送ることになる。
        """
        if not self._for_me(heard):
            return ()
        async with self._one_at_a_time:
            async with thinking():
                return self._parted(await asyncio.to_thread(self._responded, heard.text))

    def _for_me(self, heard: Heard) -> bool:
        """持ち主本人からの、中身のある直接の会話だけに答える（ADR-019）。"""
        return (
            heard.direct
            and not heard.from_myself
            and heard.author_id == self._owner_id
            and bool(heard.text.strip())
        )

    def _responded(self, utterance: str) -> str:
        """一往復を通す。応対を作れなければ手元へ理由を書き、覚えさせない。

        理由には対話する道具の出力がそのまま入る（手元の場所や道具の内部の文言が
        混ざりうる）。Discord は外の相手なので、送るのは応対できないことだけにする。
        """
        try:
            return self._turn.respond_to(self._dweller.id, utterance).reply
        except (CannotSpeak, EmbeddingsUnavailable) as reason:
            self._note(f"応対を作れませんでした: {reason}")
            return CANNOT_RESPOND

    def _note(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")
        self._writing.flush()

    def _parted(self, reply: str) -> tuple[str, ...]:
        """受け取れる長さで分ける。改行の切れ目を優先し、無ければそのまま切る。"""
        # Discord は空の本文を受け取らない。声が何も返さなかったときのための最後の一手。
        letters = reply.strip() or "（応対がありませんでした）"
        parts: list[str] = []
        while len(letters) > LETTER_LIMIT:
            cut = letters.rfind("\n", 0, LETTER_LIMIT + 1)
            if cut <= 0:
                cut = LETTER_LIMIT
            parts.append(letters[:cut].rstrip())
            letters = letters[cut:].lstrip("\n")
        parts.append(letters)
        return tuple(one for one in parts if one)

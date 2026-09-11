"""手元の対話する道具を通して応対の文章を作る。

持ち主の定額契約で動かすため、AIモデルの提供元へ直接つながない。道具の呼び方は
判定と同じ部品（`ClaudeCodeCall`）を使い、ここは渡す文章だけを組む。

AIモデルの側のやりとりは続けない。続けると記憶が二つになり、食い違ったときに
どちらが本当かを決められない。

気持ちがどう動いたかは、返事の末尾に決まった形の一行で書かせて切り出す。呼び出しを
増やさず、AIモデルが決める範囲を「今回どう動いたか」に閉じる（ADR-007）。
"""

from __future__ import annotations

from typing import Protocol, final

from yadori.adapter.tool import ClaudeWords, ToolCallFailed
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import Recollection

WAIT_SECONDS = 180


class _Call(Protocol):
    """道具を起こして返事を受け取る口。判定と同じ形で、テストは決まった文を返すものを差し込む。"""

    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ClaudeCodeVoice:
    def __init__(self, call: _Call) -> None:
        self._call: _Call = call
        self._words: ClaudeWords = ClaudeWords()

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        """名乗りと思い出したことから、応対の文章を作る。

        - 名乗りと今の気持ちと思い出しを前置きにまとめる
        - 直近のやりとりを添えて話しかけ、動きの一行を頼む
        - 道具を起こして応対を受け取り、動きの一行を切り出す
        """
        answered = self._ask(
            self._words.preface(recollection), self._words.spoken(recollection, utterance)
        )
        return self._words.parted(answered)

    def _ask(self, preface: str, spoken: str) -> str:
        """道具を起こして応対を受け取る。作れなければ、覚えさせないために断る。"""
        try:
            return self._call.ask(preface, spoken)
        except ToolCallFailed as trouble:
            raise CannotSpeak(str(trouble)) from trouble

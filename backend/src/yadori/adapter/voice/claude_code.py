"""手元の対話する道具を通して応対の文章を作る。

持ち主の定額契約で動かすため、AIモデルの提供元へ直接つながない。道具の呼び方は
判定と同じ部品（`ClaudeCodeCall`）を使い、ここは渡す文章だけを組む。

AIモデルの側のやりとりは続けない。続けると記憶が二つになり、食い違ったときに
どちらが本当かを決められない。

気持ちがどう動いたかは、返事の末尾に決まった形の一行で書かせて切り出す。呼び出しを
増やさず、AIモデルが決める範囲を「今回どう動いたか」に閉じる（ADR-007）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol, final

from yadori.adapter.tool import ToolCallFailed
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import Moved, Recollection

WAIT_SECONDS = 180
MOVED_MARK = "【気持ち】"
# 印の行。数と一言。数は −1.0〜+1.0 のつもりだが、範囲外で返っても収める。
# 全角の数字や符号は先に半角へ寄せてから読む。
MOVED_LINE = re.compile(r"^【気持ち】\s*([+-]?\d+(?:\.\d+)?)(?![\d.,])\s*(.*?)\s*$")
HOW_TO_TELL = (
    "返事の最後に、この往復であなたの気持ちがどう動いたかを"
    + f"「{MOVED_MARK}<−1.0〜+1.0 の数> <一言>」の形で一行だけ書いてください。"
    + "沈むほうへ動けば負、明るいほうへ動けば正、動かなければ 0 です。"
)


class _Call(Protocol):
    """道具を起こして返事を受け取る口。判定と同じ形で、テストは決まった文を返すものを差し込む。"""

    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ClaudeCodeVoice:
    def __init__(self, call: _Call) -> None:
        self._call: _Call = call

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        """名乗りと思い出したことから、応対の文章を作る。

        - 名乗りと今の気持ちと思い出しを前置きにまとめる
        - 直近のやりとりを添えて話しかけ、動きの一行を頼む
        - 道具を起こして応対を受け取り、動きの一行を切り出す
        """
        answered = self._ask(self._preface(recollection), self._spoken(recollection, utterance))
        return self._parted(answered)

    def _preface(self, recollection: Recollection) -> str:
        """名乗りを先に置き、今の気持ち、思い出したことを続ける。"""
        preface = [recollection.identity.text]
        preface.append(
            f"\nいまのあなたの気持ちは「{recollection.mood.described}」"
            + f"（{recollection.mood.value:+.2f}。−1 が沈む、+1 が明るい）です。"
        )
        if recollection.found:
            preface.append(
                "\n以下は、いま話しかけられた内容から思い出したことです。"
                + "会話に出ていなくても、あなたは覚えています。"
            )
            preface.extend(
                f"- {one.episode.happened_at:%Y-%m-%d} "
                + f"「{one.episode.utterance}」に「{one.episode.reply}」と答えた"
                for one in recollection.found
            )
        return "\n".join(preface)

    def _spoken(self, recollection: Recollection, utterance: str) -> str:
        """直近のやりとりを添えて、いま話しかけられた文章を渡す。

        一往復ごとに新しく呼ぶため、直前の流れはここで渡す。
        """
        lines: list[str] = []
        if recollection.recent:
            lines.append("これまでのやりとりです。")
            lines.extend(
                f"相手「{episode.utterance}」／あなた「{episode.reply}」"
                for episode in recollection.recent
            )
            lines.append("")
        lines.append(f"いま話しかけられました。これに答えてください。\n{utterance}")
        lines.append(f"\n{HOW_TO_TELL}")
        return "\n".join(lines)

    def _ask(self, preface: str, spoken: str) -> str:
        """道具を起こして応対を受け取る。作れなければ、覚えさせないために断る。"""
        try:
            return self._call.ask(preface, spoken)
        except ToolCallFailed as trouble:
            raise CannotSpeak(str(trouble)) from trouble

    def _parted(self, answered: str) -> Spoken:
        """印の行を返事から取り除き、最後の印の行から動きを読む。

        印の行は、数を読めなくても返事には残さない（話す人に印を見せない）。読めなければ
        動きなし。印を除いて返事が空なら、応対を作れなかったとして断り、覚えさせない。
        """
        lines = answered.splitlines()
        marked = [line.strip() for line in lines if line.strip().startswith(MOVED_MARK)]
        reply = "\n".join(line for line in lines if not line.strip().startswith(MOVED_MARK))
        if not reply.strip():
            raise CannotSpeak("対話する道具の返事が、気持ちの印の行だけで空だった")
        return Spoken(
            reply=reply.strip(), moved=self._moved(marked[-1]) if marked else Moved.unmoved()
        )

    def _moved(self, line: str) -> Moved:
        """印の行から動きを読む。全角は半角へ寄せ、範囲外は −1〜+1 に収める。"""
        normalized = unicodedata.normalize("NFKC", line).replace("−", "-")
        found = MOVED_LINE.match(normalized)
        if found is None:
            return Moved.unmoved()
        delta = float(found.group(1))
        return Moved(delta=max(-1.0, min(1.0, delta)), cause=found.group(2) or "理由なし")

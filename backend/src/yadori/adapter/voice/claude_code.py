"""手元の対話する道具を通して応対の文章を作る。

持ち主の定額契約で動かすため、模型の提供元へ直接つながない。道具の呼び方は
判定と同じ部品（`ClaudeCodeCall`）を使い、ここは渡す文章だけを組む。

模型の側のやりとりは続けない。続けると記憶が二つになり、食い違ったときに
どちらが本当かを決められない。
"""

from __future__ import annotations

from typing import final

from yadori.adapter.tool import ClaudeCodeCall, ToolCallFailed
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection

WAIT_SECONDS = 180


@final
class ClaudeCodeVoice:
    def __init__(self, model: str) -> None:
        self._call: ClaudeCodeCall = ClaudeCodeCall(model, WAIT_SECONDS)

    def speak(self, recollection: Recollection, utterance: str) -> str:
        """名乗りと思い出したことから、応対の文章を作る。

        - 名乗りと思い出しを前置きにまとめる
        - 直近のやりとりを添えて話しかける
        - 道具を起こして応対を受け取る
        """
        return self._ask(self._preface(recollection), self._spoken(recollection, utterance))

    def _preface(self, recollection: Recollection) -> str:
        """名乗りを先に置き、思い出したことを続ける。"""
        preface = [recollection.identity.text]
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
        if not recollection.recent:
            return utterance
        lines = ["これまでのやりとりです。"]
        lines.extend(
            f"相手「{episode.utterance}」／あなた「{episode.reply}」"
            for episode in recollection.recent
        )
        lines.append(f"\nいま話しかけられました。これに答えてください。\n{utterance}")
        return "\n".join(lines)

    def _ask(self, preface: str, spoken: str) -> str:
        """道具を起こして応対を受け取る。作れなければ、覚えさせないために断る。"""
        try:
            return self._call.ask(preface, spoken)
        except ToolCallFailed as trouble:
            raise CannotSpeak(str(trouble)) from trouble

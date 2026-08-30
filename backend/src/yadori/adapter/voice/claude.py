"""模型を呼んで応対の文章を作る。

名乗りは常に前へ置き、思い出したことは二つの道を分けたまま渡す。直近の
やりとりは会話として、探した記憶は思い出しとして渡す。混ぜると、模型から
見てどちらが今の話でどちらが昔の話か分からなくなる。
"""

from __future__ import annotations

from typing import final

import anthropic
from anthropic.types.beta import BetaMessageParam

from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection

MODEL = "claude-opus-5"
MAX_TOKENS = 4096
# 断られたときに別の模型で続ける。人格を持つ相手なので、断りがそのまま
# 沈黙になると会話が途切れる。
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@final
class ClaudeVoice:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client: anthropic.Anthropic = client or anthropic.Anthropic()

    def speak(self, recollection: Recollection, utterance: str) -> str:
        """名乗りと思い出したことから、応対の文章を作る。

        - 名乗りと思い出しを前置きにまとめる
        - 直近のやりとりを会話として並べる
        - 模型へ渡して文章を受け取る
        """
        system = self._preface(recollection)
        spoken: BetaMessageParam = {"role": "user", "content": utterance}
        messages = [*self._as_conversation(recollection), spoken]
        return self._ask(system, messages)

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

    def _as_conversation(self, recollection: Recollection) -> list[BetaMessageParam]:
        """直近のやりとりを、そのまま会話として並べる。"""
        turns: list[BetaMessageParam] = []
        for episode in recollection.recent:
            turns.append({"role": "user", "content": episode.utterance})
            turns.append({"role": "assistant", "content": episode.reply})
        return turns

    def _ask(self, system: str, messages: list[BetaMessageParam]) -> str:
        """模型へ渡す。応対が返らなければ、覚えさせないために断る。"""
        try:
            response = self._client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as error:
            raise CannotSpeak(f"模型を呼べなかった: {error}") from error

        if response.stop_reason == "refusal":
            raise CannotSpeak("模型が応対を断った")
        spoken = "".join(block.text for block in response.content if block.type == "text")
        if not spoken.strip():
            raise CannotSpeak("応対が空だった")
        return spoken

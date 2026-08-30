"""手元の対話する道具を通して応対の文章を作る。

持ち主の定額契約で動かすため、模型の提供元へ直接つながない。

その道具が普段持ち込む文脈は必ず外す。外さないと、道具自身の指示と持ち主が
手元に置いた指示が前置きとして渡り、名乗りより強く出る。実測では前置きが
約六万語から三百語ほどへ減り、名乗りへ書いていない話し方も現れなくなった。

模型の側のやりとりは続けない。続けると記憶が二つになり、食い違ったときに
どちらが本当かを決められない。
"""

from __future__ import annotations

import json
import subprocess
from typing import final

from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection

# その道具が普段持ち込むものを外す。どれか一つでも欠けると混ざる。
WITHOUT_ITS_OWN_CONTEXT = ["--restricted", "--strict-mcp-config", "--tools", ""]
WAIT_SECONDS = 180


@final
class ClaudeCodeVoice:
    def __init__(self, model: str) -> None:
        self._model: str = model

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
            done = subprocess.run(
                [
                    "claude",
                    "-p",
                    spoken,
                    "--system-prompt",
                    preface,
                    "--model",
                    self._model,
                    "--output-format",
                    "json",
                    *WITHOUT_ITS_OWN_CONTEXT,
                ],
                capture_output=True,
                text=True,
                timeout=WAIT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as trouble:
            raise CannotSpeak(f"対話する道具を呼べなかった: {trouble}") from trouble

        if done.returncode != 0:
            raise CannotSpeak(f"対話する道具が失敗した: {done.stderr.strip()[:200]}")
        return self._as_reply(done.stdout)

    def _as_reply(self, written: str) -> str:
        """道具の返した記録から、応対の文章だけを取り出す。"""
        try:
            # 外から来る記録なので型を約束しない。取り出すときに確かめる。
            answered: object = json.loads(written)  # pyright: ignore[reportAny]
        except json.JSONDecodeError as broken:
            raise CannotSpeak("対話する道具の返事を読めなかった") from broken
        if not isinstance(answered, dict):
            raise CannotSpeak("対話する道具の返事の形が違う")
        for key, value in answered.items():  # pyright: ignore[reportUnknownVariableType]
            if key == "result" and isinstance(value, str) and value.strip():
                return value
        raise CannotSpeak("応対が空だった")

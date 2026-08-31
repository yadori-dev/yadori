"""手元の対話する道具（Claude Code）を、その道具の文脈を外して呼ぶ。

声（応対を作る）と判定（組を出す）が同じ呼び方を使う。呼び方が二箇所にあると、
道具が変わったとき片方だけ直る。渡す文章と待ち時間だけが呼ぶ側で違う。

その道具が普段持ち込む文脈は必ず外す（ADR-016）。外さないと、道具自身の指示と
持ち主が手元に置いた指示が前置きとして渡り、こちらの文章より強く出る。
"""

from __future__ import annotations

import json
import subprocess
from typing import final

# その道具が普段持ち込むものを外す。どれか一つでも欠けると混ざる。
WITHOUT_ITS_OWN_CONTEXT = ["--restricted", "--strict-mcp-config", "--tools", ""]


class ToolCallFailed(Exception):
    """道具を呼べなかった、または返事を読めなかった。呼ぶ側が自分の失敗へ言い換える。"""


@final
class ClaudeCodeCall:
    def __init__(self, model: str, wait_seconds: int) -> None:
        self._model: str = model
        self._wait_seconds: int = wait_seconds

    def ask(self, preface: str, spoken: str) -> str:
        """前置きと文章を渡し、返事の文章だけを受け取る。"""
        return self._as_reply(self._run(preface, spoken))

    def _run(self, preface: str, spoken: str) -> str:
        try:
            # 文章は標準入力で渡す。引数で渡すと、長い並びで OS の上限に当たる。
            done = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--system-prompt",
                    preface,
                    "--model",
                    self._model,
                    "--output-format",
                    "json",
                    *WITHOUT_ITS_OWN_CONTEXT,
                ],
                input=spoken,
                capture_output=True,
                text=True,
                timeout=self._wait_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as trouble:
            raise ToolCallFailed(f"対話する道具を呼べなかった: {trouble}") from trouble
        if done.returncode != 0:
            raise ToolCallFailed(f"対話する道具が失敗した: {done.stderr.strip()[:200]}")
        return done.stdout

    def _as_reply(self, written: str) -> str:
        """道具の返した記録から、返事の文章だけを取り出す。"""
        try:
            # 外から来る記録なので型を約束しない。取り出すときに確かめる。
            answered: object = json.loads(written)  # pyright: ignore[reportAny]
        except json.JSONDecodeError as broken:
            raise ToolCallFailed("対話する道具の返事を読めなかった") from broken
        if not isinstance(answered, dict):
            raise ToolCallFailed("対話する道具の返事の形が違う")
        for key, value in answered.items():  # pyright: ignore[reportUnknownVariableType]
            if key == "result" and isinstance(value, str) and value.strip():
                return value
        raise ToolCallFailed("対話する道具の返事が空だった")

"""Claude Code へ渡す言葉と、返事から取り出す一往復。"""

from __future__ import annotations

import json
from typing import final, override

from yadori.adapter.tool.companion_words import (
    HOW_TO_TELL,
    MOVED_LINE,
    MOVED_MARK,
    CompanionWords,
)

__all__ = ["HOW_TO_TELL", "MOVED_LINE", "MOVED_MARK", "ClaudeWords"]


@final
class ClaudeWords(CompanionWords):
    """名乗りと記憶を Claude Code の文へし、返事を本文と動きへ戻す。"""

    @override
    def _json(self, context: str) -> str:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

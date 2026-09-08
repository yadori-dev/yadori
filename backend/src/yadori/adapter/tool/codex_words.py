"""Codex へ渡す言葉と、返事から取り出す一往復。"""

from __future__ import annotations

import json
from typing import final, override

from yadori.adapter.tool.companion_words import (
    HOW_TO_TELL,
    MOVED_LINE,
    MOVED_MARK,
    CompanionWords,
)
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection

__all__ = ["HOW_TO_TELL", "MOVED_LINE", "MOVED_MARK", "CodexWords"]


@final
class CodexWords(CompanionWords):
    """名乗りと記憶を Codex の文にし、返事を本文と動きへ戻す。"""

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

    def compact_response(self, recollection: Recollection, limit: int = 9000) -> str:
        """会話圧縮（compact）後に名乗りと状態を戻すフック応答。"""
        context = self.identity_and_state(recollection)
        if len(self._compact_json(context).encode("utf-8")) > limit:
            raise CannotSpeak("宿りの名乗りと状態だけで Codex のフック上限を超えた")
        return self._compact_json(context)

    def _compact_json(self, context: str) -> str:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

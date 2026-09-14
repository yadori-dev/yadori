"""agy の一時文脈へ宿りの言葉を渡す。"""

from __future__ import annotations

import json
from typing import final, override

from yadori.adapter.tool.companion_words import CompanionWords


@final
class AgyWords(CompanionWords):
    @override
    def _json(self, context: str) -> str:
        return json.dumps(
            {"injectSteps": [{"ephemeralMessage": context}]},
            ensure_ascii=False,
            separators=(",", ":"),
        )

"""補完の保存形式。原文や索引をこの文へ混ぜない。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from yadori.domain.memory import Clarification


class ClarificationData:
    @classmethod
    def dump(cls, value: Clarification) -> str:
        return json.dumps(
            {
                "episode_id": value.episode_id,
                "revision": value.revision,
                "status": value.status,
                "kind": value.kind,
                "text": value.text,
                "reason": value.reason,
                "made_at": value.made_at.isoformat(),
                "sources": value.sources,
            },
            ensure_ascii=False,
        )

    @classmethod
    def load(cls, text: str) -> Clarification:
        raw: object = json.loads(text)  # pyright: ignore[reportAny]
        if not isinstance(raw, dict):
            raise ValueError("補完の形式が違う")
        data: dict[str, object] = {str(key): value for key, value in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise ValueError("根拠が並びではない")
        return Clarification(
            cls.number(data.get("episode_id")),
            cls.number(data.get("revision")),
            cls.status(data.get("status")),
            cls.kind(data.get("kind")),
            None if data.get("text") is None else cls.text(data.get("text")),
            cls.text(data.get("reason")),
            datetime.fromisoformat(cls.text(data.get("made_at"))),
            tuple(cls.number(one) for one in sources),  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        )

    @staticmethod
    def number(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("補完の番号が整数ではない")
        return value

    @staticmethod
    def text(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("補完の文字列が空か形式が違う")
        return value

    @staticmethod
    def status(value: object) -> Literal["clarified", "deferred", "unneeded"]:
        if value == "clarified":
            return "clarified"
        if value == "deferred":
            return "deferred"
        if value == "unneeded":
            return "unneeded"
        raise ValueError("補完の状態が未知")

    @staticmethod
    def kind(value: object) -> Literal["approval", "denial", "continuation", "selection"] | None:
        if value is None:
            return None
        if value == "approval":
            return "approval"
        if value == "denial":
            return "denial"
        if value == "continuation":
            return "continuation"
        if value == "selection":
            return "selection"
        raise ValueError("応答種別が未知")

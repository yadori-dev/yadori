"""実 agy の通知と記録から、主担当の一往復を読む。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import final


@final
class AgyJson:
    """外から届く JSON の形を入口で確かめる。"""

    @staticmethod
    def object(text: str) -> dict[str, object]:
        value: object = json.loads(text)  # pyright: ignore[reportAny]
        if not isinstance(value, dict):
            raise ValueError("agy の記録が項目の組ではありません")
        result: dict[str, object] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise ValueError("agy の項目名が文字列ではありません")
            result[key] = item
        return result

    @staticmethod
    def text(data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"agy の記録に {key} がありません")
        return value

    @staticmethod
    def number(data: dict[str, object], key: str) -> int:
        value = data.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"agy の記録の {key} が非負の整数ではありません")
        return value

    @staticmethod
    def write(path: Path, data: dict[str, object]) -> None:
        temporary = path.with_suffix(".new")
        with temporary.open("w", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(data, ensure_ascii=False))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        _ = temporary.replace(path)


@dataclass(frozen=True)
class AgyNotice:
    """会話と発話を区別し、途中の返事を完成済みにしない通知。"""

    conversation_id: str
    step: int
    utterance: str
    reply: str | None
    finished: bool
    previous_source: str | None = None

    @property
    def source(self) -> str:
        return f"agy:{self.conversation_id}:{self.step}"

    @classmethod
    def read(cls, event: str, text: str, run_dir: Path) -> AgyNotice:
        payload = AgyJson.object(text)
        conversation_id = AgyJson.text(payload, "conversationId")
        if re.fullmatch(r"[0-9a-f-]{36}", conversation_id) is None:
            raise ValueError("agy の会話の識別子を読めません")
        path = run_dir / "gemini/antigravity-cli/brain" / conversation_id
        path = path / ".system_generated/logs/transcript_full.jsonl"
        if not path.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("agy の記録が専用領域の外を指しています")
        lines = [AgyJson.object(line) for line in path.read_text(encoding="utf-8").splitlines()]
        users = [
            row
            for row in lines
            if row.get("type") == "USER_INPUT" and row.get("source") == "USER_EXPLICIT"
        ]
        if not users:
            raise ValueError("agy の記録に持ち主の発話がありません")
        user = users[-1]
        step = AgyJson.number(user, "step_index")
        content = AgyJson.text(user, "content")
        found = re.fullmatch(
            r"<USER_REQUEST>\n(.*)\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n.*", content, re.S
        )
        if found is None or not found.group(1).strip():
            raise ValueError("agy の発話の形式が対応する版と異なります")
        finished = (
            event == "stop"
            and payload.get("fullyIdle") is True
            and payload.get("terminationReason") == "NO_TOOL_CALL"
            and payload.get("error") == ""
        )
        if (
            event == "stop"
            and payload.get("fullyIdle") is True
            and not payload.get("error")
            and not finished
        ):
            raise ValueError("agy の停止理由が対応する版と異なります")
        reply: str | None = None
        if finished:
            final_row = lines[-1]
            if (
                final_row.get("type") != "PLANNER_RESPONSE"
                or final_row.get("source") != "MODEL"
                or final_row.get("status") != "DONE"
                or final_row.get("tool_calls")
                or AgyJson.number(final_row, "step_index") <= step
            ):
                raise ValueError("agy の正常終了に対応する最後の返事を確認できません")
            reply = AgyJson.text(final_row, "content")
        previous = (
            f"agy:{conversation_id}:{AgyJson.number(users[-2], 'step_index')}"
            if len(users) > 1
            else None
        )
        return cls(conversation_id, step, found.group(1), reply, finished, previous)

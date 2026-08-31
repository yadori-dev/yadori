"""Codex が手元に残す記録の読み手。

記録は一行ごとの JSON で、先頭に `session_meta`、続いて `response_item` として
発言が並ぶ。利用者の発言には道具が入れる作業場所の説明や指示の写しが混ざる
ため、ここで除く。短い相槌のような中身の無さは記録の一往復の値が答える。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from yadori.domain.evaluation import BrokenRecord, Recorded

# 利用者の発言に混ざる、道具の側が入れる文章の印。
NOISE = ("<environment_context>", "<user_instructions>", "# AGENTS.md", "<permissions")


@final
class CodexRecords:
    def claims(self, path: Path) -> bool:
        first = self._first_line(path)
        return first is not None and first.get("type") == "session_meta"

    def read(self, path: Path) -> tuple[Recorded, ...]:
        rows = self._rows(path)
        session, workspace = self._session_and_workspace(rows, path)
        recorded: list[Recorded] = []
        for index, row in enumerate(rows):
            utterance = self._utterance(row)
            if utterance is None:
                continue
            recorded.append(
                Recorded(
                    session=session,
                    workspace=workspace,
                    at=self._at(row),
                    utterance=utterance,
                    reply=self._reply_after(rows, index),
                )
            )
        return tuple(recorded)

    def _first_line(self, path: Path) -> dict[str, object] | None:
        try:
            with path.open(encoding="utf-8", errors="replace") as opened:
                for line in opened:
                    if line.strip():
                        parsed: object = json.loads(line)  # pyright: ignore[reportAny]
                        return parsed if isinstance(parsed, dict) else None  # pyright: ignore[reportUnknownVariableType]
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _rows(self, path: Path) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                parsed: object = json.loads(line)  # pyright: ignore[reportAny]
            except json.JSONDecodeError as broken:
                raise BrokenRecord(f"{path.name}:{number} が読めない") from broken
            if isinstance(parsed, dict):
                rows.append(parsed)  # pyright: ignore[reportUnknownArgumentType]
        return rows

    def _session_and_workspace(self, rows: list[dict[str, object]], path: Path) -> tuple[str, str]:
        for row in rows:
            payload = self._payload(row)
            if row.get("type") == "session_meta" and payload is not None:
                return str(payload.get("id", path.stem)), str(payload.get("cwd", ""))
        raise BrokenRecord(f"{path.name} に session_meta が無い")

    def _payload(self, row: dict[str, object]) -> dict[str, object] | None:
        payload: object = row.get("payload")
        return payload if isinstance(payload, dict) else None  # pyright: ignore[reportUnknownVariableType]

    def _message(self, row: dict[str, object], role: str) -> dict[str, object] | None:
        payload = self._payload(row)
        if row.get("type") != "response_item" or payload is None:
            return None
        if payload.get("type") != "message" or payload.get("role") != role:
            return None
        return payload

    def _utterance(self, row: dict[str, object]) -> str | None:
        message = self._message(row, "user")
        if message is None:
            return None
        text = self._text_of(message, "input_text")
        if text is None or any(noise in text for noise in NOISE):
            return None
        return text

    def _reply_after(self, rows: list[dict[str, object]], index: int) -> str:
        for row in rows[index + 1 :]:
            if self._message(row, "user") is not None:
                return ""
            answered = self._message(row, "assistant")
            if answered is not None:
                text = self._text_of(answered, "output_text")
                if text:
                    return text
        return ""

    def _text_of(self, message: dict[str, object], kind: str) -> str | None:
        content: object = message.get("content")
        if not isinstance(content, list):
            return None
        texts: list[str] = []
        for part in content:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(part, dict) and part.get("type") == kind:  # pyright: ignore[reportUnknownMemberType]
                text: object = part.get("text", "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts) or None

    def _at(self, row: dict[str, object]) -> datetime:
        written = row.get("timestamp")
        if not isinstance(written, str):
            raise BrokenRecord("時刻が無い")
        return datetime.fromisoformat(written.replace("Z", "+00:00")).astimezone(UTC)

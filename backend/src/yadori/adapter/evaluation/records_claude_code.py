"""Claude Code が手元に残す記録の読み手。

記録は一行ごとの JSON で、利用者と道具の発言が `type` で分かれる。ここでは
形式に依る雑音（道具の命令、貼り付けた出力、画像だけの発話、割り込みの印）を
除く。短い相槌のような中身の無さは記録の一往復の値が答え、ここでは見ない。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from yadori.domain.evaluation import BrokenRecord, Recorded

# 利用者の発話に混ざる、道具の側が入れる文章の印。
NOISE = (
    "<command-name>",
    "<command-message>",
    "<local-command",
    "<system-reminder>",
    "<bash-input>",
    "<bash-stdout",
    "<bash-stderr",
    "<task-notification",
    "[Request interrupted",
)


@final
class ClaudeCodeRecords:
    def claims(self, path: Path) -> bool:
        """先頭行に、この形式だけが持つ項目があるか。"""
        first = self._first_line(path)
        return first is not None and "sessionId" in first and "type" in first

    def read(self, path: Path) -> tuple[Recorded, ...]:
        rows = self._rows(path)
        recorded: list[Recorded] = []
        for index, row in enumerate(rows):
            utterance = self._utterance(row)
            if utterance is None:
                continue
            recorded.append(
                Recorded(
                    session=str(row.get("sessionId", path.stem)),
                    workspace=str(row.get("cwd", "")),
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

    def _utterance(self, row: dict[str, object]) -> str | None:
        """利用者の発話の文章。道具の結果や雑音を含む行は発話としない。"""
        # 子エージェントへ渡した指示（isSidechain）は道具が作った文章で、利用者の発話ではない。
        if row.get("type") != "user" or row.get("isMeta") or row.get("isSidechain"):
            return None
        text = self._text_of(row.get("message"), allow_tool_result=False)
        if text is None:
            return None
        if any(noise in text for noise in NOISE):
            return None
        return text

    def _reply_after(self, rows: list[dict[str, object]], index: int) -> str:
        """直後の返事。道具の結果は `user` の行として記録されるが、利用者の発話ではないので
        そこで打ち切らず、次の本物の発話まで読む。"""
        for row in rows[index + 1 :]:
            if self._utterance(row) is not None:
                return ""
            if row.get("type") == "assistant":
                text = self._text_of(row.get("message"), allow_tool_result=True)
                if text:
                    return text
        return ""

    def _text_of(self, message: object, *, allow_tool_result: bool) -> str | None:
        """発言の文章。画像だけ、道具の結果だけの発言は文章を持たない。"""
        if not isinstance(message, dict):
            return None
        content: object = message.get("content")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(content, str):
            return content.strip() or None
        if not isinstance(content, list):
            return None
        texts: list[str] = []
        for part in content:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(part, dict):
                continue
            kind: object = part.get("type")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if kind == "tool_result" and not allow_tool_result:
                return None
            if kind == "text":
                text: object = part.get("text", "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts) or None

    def _at(self, row: dict[str, object]) -> datetime:
        written = row.get("timestamp")
        if not isinstance(written, str):
            raise BrokenRecord("時刻が無い")
        try:
            return datetime.fromisoformat(written.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as broken:
            raise BrokenRecord(f"時刻が読めない: {written!r}") from broken

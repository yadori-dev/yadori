"""同じ起動で直接続いた通知だけを結ぶ。中断した往復を飛び越さない。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import final


@final
class Continuity:
    def __init__(self, run_dir: Path) -> None:
        self._directory = run_dir / "continuity"

    def begin(self, session: str, source: str) -> str | None:
        state = self._read(session)
        if state.get("current") == source:
            return state.get("previous")
        previous = state.get("last")
        self._write(session, {"current": source, "previous": previous, "last": None})
        return previous

    def finish(self, session: str, source: str) -> None:
        state = self._read(session)
        if state.get("current") == source:
            state["last"] = source
            self._write(session, state)

    def interrupt(self, session: str) -> None:
        self._write(session, {"current": None, "previous": None, "last": None})

    def _path(self, session: str) -> Path:
        return self._directory / (hashlib.sha256(session.encode()).hexdigest() + ".json")

    def _read(self, session: str) -> dict[str, str | None]:
        path = self._path(session)
        if not path.exists():
            return {}
        raw: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
        if not isinstance(raw, dict):
            raise ValueError("会話の連続性を読めない")
        state: dict[str, str | None] = {}
        for key in ("current", "previous", "last"):
            value: object = raw.get(key)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if value is not None and not isinstance(value, str):
                raise ValueError("会話の連続性の出典が文字列ではない")
            state[key] = value
        return state

    def _write(self, session: str, state: dict[str, str | None]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(session)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temporary.replace(path)

"""一回の起動で返事を待つ未完の発話と、完了した往復の再送確認。

対話する道具（Claude Code、Codex）が返事を作る途中で止めた発話を捨て、
同じ停止通知の再送で記憶が二重にならないようにする。
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from yadori.domain.conversation import Spoken
from yadori.domain.memory import Moved


@dataclass(frozen=True)
class PendingTurn:
    """返事または停止を待っている一往復。"""

    session_id: str
    turn_id: str
    utterance: str
    recalled_at: datetime
    identity_version: int
    context: str
    spoken: Spoken | None = None
    previous_source: str | None = None


@final
class PendingStore:
    """専用領域の中で未完の発話と完了済みの往復を安全に管理する。"""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def read(self, session_id: str) -> PendingTurn | None:
        path = self._pending_path(session_id)
        if not path.exists():
            return None
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
            data = self._mapping(raw)
            if data is None:
                return None
            spoken_data = self._mapping(data.get("spoken"))
            spoken: Spoken | None = None
            if spoken_data is not None:
                moved_data = self._mapping(spoken_data.get("moved"))
                moved = (
                    Moved(
                        delta=float(str(moved_data["delta"])),
                        cause=str(moved_data.get("cause", "理由なし")),
                    )
                    if moved_data is not None and "delta" in moved_data
                    else Moved.unmoved()
                )
                spoken = Spoken(reply=str(spoken_data.get("reply", "")), moved=moved)
            elif "reply" in data and isinstance(data["reply"], str):
                delta = float(str(data.get("delta", 0.0)))
                cause = str(data.get("cause", "理由なし"))
                spoken = Spoken(reply=data["reply"], moved=Moved(delta=delta, cause=cause))
            return PendingTurn(
                session_id=str(data["session_id"]),
                turn_id=str(data["turn_id"]),
                utterance=str(data["utterance"]),
                recalled_at=datetime.fromisoformat(str(data["recalled_at"])),
                identity_version=int(str(data["identity_version"])),
                context=str(data["context"]),
                spoken=spoken,
                previous_source=self._optional_source(data.get("previous_source")),
            )
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def _optional_source(self, value: object) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("先行往復の出典が文字列ではない")
        return value

    def save(self, pending: PendingTurn) -> None:
        path = self._pending_path(pending.session_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload: dict[str, object] = {
            "session_id": pending.session_id,
            "turn_id": pending.turn_id,
            "utterance": pending.utterance,
            "recalled_at": pending.recalled_at.isoformat(),
            "identity_version": pending.identity_version,
            "context": pending.context,
            "previous_source": pending.previous_source,
        }
        if pending.spoken is not None:
            payload["reply"] = pending.spoken.reply
            payload["delta"] = pending.spoken.moved.delta
            payload["cause"] = pending.spoken.moved.cause
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened:
            json.dump(payload, opened, ensure_ascii=False, separators=(",", ":"))
        _ = temporary.replace(path)

    def discard(self, session_id: str) -> None:
        path = self._pending_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def read_completed(self, turn_id: str) -> Spoken | None:
        path = self._completed_path(turn_id)
        if not path.exists():
            return None
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
            data = self._mapping(raw)
            if data is None:
                return None
            reply = str(data.get("reply", ""))
            delta = float(str(data.get("delta", 0.0)))
            cause = str(data.get("cause", "理由なし"))
            return Spoken(reply=reply, moved=Moved(delta=delta, cause=cause))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def mark_completed(self, turn_id: str, spoken: Spoken) -> None:
        path = self._completed_path(turn_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload: dict[str, object] = {
            "reply": spoken.reply,
            "delta": spoken.moved.delta,
            "cause": spoken.moved.cause,
        }
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.done")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened:
            json.dump(payload, opened, ensure_ascii=False, separators=(",", ":"))
        _ = temporary.replace(path)

    def _mapping(self, value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

    def _pending_path(self, session_id: str) -> Path:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self._run_dir / "pending" / f"{safe_id}.json"

    def _completed_path(self, turn_id: str) -> Path:
        safe_id = "".join(c for c in turn_id if c.isalnum() or c in "-_")
        return self._run_dir / "completed" / f"{safe_id}.done"


@final
class StaleSessionCleaner:
    """施錠の無い24時間以上前のセッションディレクトリを片付ける。"""

    @staticmethod
    def clean(sessions_dir: Path, age: timedelta | None = None) -> None:
        if not sessions_dir.exists():
            return
        limit = age or timedelta(hours=24)
        now = datetime.now(UTC)
        for child in sessions_dir.iterdir():
            if not child.is_dir():
                continue
            lock_path = child / "session.lock"
            if not lock_path.exists():
                continue
            try:
                modified = datetime.fromtimestamp(child.stat().st_mtime, tz=UTC)
                if now - modified < limit:
                    continue
                descriptor = os.open(lock_path, os.O_RDWR)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    shutil.rmtree(child, ignore_errors=True)
                finally:
                    os.close(descriptor)
            except OSError:
                continue

"""Claude Code を宿りとして起こし、正式なフックを会話の二つの口へ繋ぐ。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ClaudeSession, ClaudeSessionError, ClaudeWords
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import EmbeddingsUnavailable, Moved, RememberingConflict
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation


@dataclass(frozen=True)
class _Pending:
    session_id: str
    prompt_id: str
    utterance: str
    recalled_at: datetime
    identity_version: int
    context: str
    spoken: Spoken | None = None


@final
class ClaudeCompanion:
    """利用者が打つ入口。宿りの準備を確かめて Claude Code を同じ端末で待つ。"""

    def __init__(
        self, home: Path | None = None, cwd: Path | None = None, startup: Startup | None = None
    ) -> None:
        self._settings_file = SettingsFile(home)
        self._cwd = cwd
        self._startup = startup or Startup(home)

    def run(self) -> int:
        try:
            settings = self._settings_file.read()
            self._prepare_memory(settings)
            print(f"（{settings.dweller.nickname} として Claude Code を起こします）")
            return ClaudeSession(settings.home, self._cwd).launch()
        except (NotSettled, EmbeddingsUnavailable, ClaudeSessionError, sqlite3.Error) as trouble:
            print(trouble, file=sys.stderr)
            return 1

    def _prepare_memory(self, settings: Settings) -> None:
        memories = SqliteMemories(settings.memories_path)
        try:
            self._startup.settle(memories, settings)
            _ = self._startup.conversation(memories, settings).rebuild_index(settings.dweller.id)
        finally:
            memories.close()


@final
class ClaudeHook:
    """一回の Claude Code だけが呼ぶ、思い出す・覚える・未完を捨てる入口。"""

    def __init__(
        self,
        event: str,
        run_dir: Path,
        home: Path | None = None,
        startup: Startup | None = None,
    ) -> None:
        self._event = event
        self._settings_file = SettingsFile(home)
        self._run_dir = run_dir
        self._words = ClaudeWords()
        self._startup = startup or Startup(home)

    def run(self) -> int:
        try:
            self._validate_run_dir()
            payload = self._payload()
            if self._event == "UserPromptSubmit":
                return self._user_prompt(payload)
            if self._event == "Stop":
                return self._stop(payload)
            if self._event == "StopFailure":
                return self._stop_failure(payload)
            if self._event == "SessionEnd":
                return self._session_end(payload)
            raise ValueError(f"知らない Claude Code のフック: {self._event}")
        except (
            OSError,
            ValueError,
            CannotSpeak,
            RememberingConflict,
            RuntimeError,
            sqlite3.Error,
            NotSettled,
            EmbeddingsUnavailable,
        ) as trouble:
            print(f"宿りのフックを処理できません: {trouble}", file=sys.stderr)
            return 2

    def _user_prompt(self, payload: dict[str, object]) -> int:
        session_id = self._identifier(payload, "session_id")
        prompt_id = self._identifier(payload, "prompt_id")
        utterance = self._text(payload, "prompt")
        old = self._read_pending(session_id)
        if old is not None and old.prompt_id == prompt_id:
            if old.utterance != utterance:
                raise ValueError("同じ prompt_id へ異なる発話が届いた")
            print(old.context)
            return 0
        if old is not None and old.spoken is not None:
            try:
                self._remember(old)
            except (CannotSpeak, RememberingConflict, RuntimeError, sqlite3.Error) as trouble:
                print(f"前の一往復を覚えられません: {trouble}", file=sys.stderr)
                return 2
        self._discard(session_id)
        settings, memories, conversation = self._conversation()
        try:
            recollection = conversation.recall(settings.dweller.id, utterance)
            context = self._words.hook_response(recollection)
            self._write_pending(
                _Pending(
                    session_id,
                    prompt_id,
                    utterance,
                    recollection.recalled_at,
                    recollection.identity.version,
                    context,
                )
            )
        finally:
            memories.close()
        print(context)
        return 0

    def _stop(self, payload: dict[str, object]) -> int:
        session_id = self._identifier(payload, "session_id")
        prompt_id = self._identifier(payload, "prompt_id")
        completed = self._read_completed(prompt_id)
        if completed is not None:
            replayed = self._words.parted(self._text(payload, "last_assistant_message"))
            if replayed != completed:
                return self._block("同じ発話へ以前と異なる返事または気持ちが届きました")
            return 0
        pending = self._read_pending(session_id)
        if pending is None or pending.prompt_id != prompt_id:
            return 0
        try:
            spoken = self._words.parted(self._text(payload, "last_assistant_message"))
            pending = _Pending(
                pending.session_id,
                pending.prompt_id,
                pending.utterance,
                pending.recalled_at,
                pending.identity_version,
                pending.context,
                spoken,
            )
            self._write_pending(pending)
            self._remember(pending)
        except (CannotSpeak, RememberingConflict, RuntimeError, sqlite3.Error) as trouble:
            return self._block(f"宿りがこの一往復を覚えられませんでした: {trouble}")
        try:
            self._mark_completed(prompt_id, spoken)
        except OSError as trouble:
            return self._block(f"完了した一往復の再送確認を残せませんでした: {trouble}")
        self._discard(session_id)
        return 0

    def _block(self, reason: str) -> int:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    def _stop_failure(self, payload: dict[str, object]) -> int:
        self._discard(self._identifier(payload, "session_id"))
        return 0

    def _session_end(self, payload: dict[str, object]) -> int:
        session_id = self._identifier(payload, "session_id")
        pending = self._read_pending(session_id)
        if pending is not None and pending.spoken is not None:
            try:
                self._remember(pending)
            except (CannotSpeak, RememberingConflict, RuntimeError, sqlite3.Error) as trouble:
                print(f"終了時に一往復を覚えられませんでした: {trouble}", file=sys.stderr)
        self._discard(session_id)
        return 0

    def _remember(self, pending: _Pending) -> None:
        if pending.spoken is None:
            return
        settings, memories, conversation = self._conversation()
        try:
            _ = conversation.remember(
                settings.dweller.id,
                pending.utterance,
                pending.spoken.reply,
                pending.spoken.moved,
                recalled_at=pending.recalled_at,
                source=f"claude:{pending.prompt_id}",
                identity_version=pending.identity_version,
            )
        finally:
            memories.close()

    def _conversation(self) -> tuple[Settings, SqliteMemories, Conversation]:
        settings = self._settings_file.read()
        memories = SqliteMemories(settings.memories_path)
        try:
            self._startup.settle(memories, settings)
            conversation = self._startup.conversation(memories, settings)
            _ = conversation.rebuild_index(settings.dweller.id)
        except BaseException:
            memories.close()
            raise
        return settings, memories, conversation

    def _pending_path(self, session_id: str) -> Path:
        return self._run_dir / "pending" / f"{session_id}.json"

    def _completed(self, prompt_id: str) -> Path:
        return self._run_dir / "completed" / f"{prompt_id}.done"

    def _write_pending(self, pending: _Pending) -> None:
        path = self._pending_path(pending.session_id)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        value: dict[str, object] = {
            "session_id": pending.session_id,
            "prompt_id": pending.prompt_id,
            "utterance": pending.utterance,
            "recalled_at": pending.recalled_at.isoformat(),
            "identity_version": pending.identity_version,
            "context": pending.context,
        }
        if pending.spoken is not None:
            value["reply"] = pending.spoken.reply
            value["delta"] = pending.spoken.moved.delta
            value["cause"] = pending.spoken.moved.cause
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened:
            json.dump(value, opened, ensure_ascii=False, separators=(",", ":"))
        _ = temporary.replace(path)

    def _read_pending(self, session_id: str) -> _Pending | None:
        path = self._pending_path(session_id)
        if not path.exists():
            return None
        value = self._json_object(path.read_text(encoding="utf-8"))
        spoken: Spoken | None = None
        reply = value.get("reply")
        delta = value.get("delta")
        cause = value.get("cause")
        if isinstance(reply, str) and isinstance(delta, int | float) and isinstance(cause, str):
            spoken = Spoken(reply, Moved(float(delta), cause))
        version = value.get("identity_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("未完の発話の名乗りの版が数ではない")
        return _Pending(
            self._text(value, "session_id"),
            self._text(value, "prompt_id"),
            self._text(value, "utterance"),
            datetime.fromisoformat(self._text(value, "recalled_at")),
            version,
            self._text(value, "context"),
            spoken,
        )

    def _discard(self, session_id: str) -> None:
        self._pending_path(session_id).unlink(missing_ok=True)

    def _read_completed(self, prompt_id: str) -> Spoken | None:
        path = self._completed(prompt_id)
        if not path.exists():
            return None
        value = self._json_object(path.read_text(encoding="utf-8"))
        delta = value.get("delta")
        if not isinstance(delta, int | float):
            raise ValueError("完了した発話の気持ちの動きが数ではない")
        return Spoken(
            self._text(value, "reply"),
            Moved(float(delta), self._text(value, "cause")),
        )

    def _mark_completed(self, prompt_id: str, spoken: Spoken) -> None:
        path = self._completed(prompt_id)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened:
            json.dump(
                {
                    "reply": spoken.reply,
                    "delta": spoken.moved.delta,
                    "cause": spoken.moved.cause,
                },
                opened,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        _ = temporary.replace(path)

    def _payload(self) -> dict[str, object]:
        return self._json_object(sys.stdin.read())

    def _json_object(self, written: str) -> dict[str, object]:
        parsed: object = json.loads(written)  # pyright: ignore[reportAny]
        if not isinstance(parsed, dict):
            raise ValueError("フックの入力が物の組ではない")
        checked: dict[str, object] = {}
        for key, value in parsed.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise ValueError("フックの入力の名前が文字ではない")
            checked[key] = value
        return checked

    def _identifier(self, payload: dict[str, object], key: str) -> str:
        value = self._text(payload, key)
        try:
            _ = uuid.UUID(value)
        except ValueError as trouble:
            raise ValueError(f"{key} が UUID ではない") from trouble
        return value

    def _text(self, payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} が文字ではない")
        return value

    def _validate_run_dir(self) -> None:
        settings = self._settings_file.read()
        sessions = (settings.home / "claude" / "sessions").resolve()
        run_dir = self._run_dir.resolve()
        if run_dir.parent != sessions or not run_dir.is_dir():
            raise ValueError("フックの一時置き場がこの宿りの起動ではない")

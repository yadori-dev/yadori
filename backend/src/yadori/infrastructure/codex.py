"""Codex を宿りとして起こし、ライフサイクルフックを会話の口へ繋ぐ。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import final

from yadori.adapter.recall.connection import RecallGuidance
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import (
    CodexSession,
    CodexSessionError,
    CodexWords,
    PendingStore,
    PendingTurn,
)
from yadori.adapter.tool.continuity import Continuity
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import EmbeddingsUnavailable, RememberingConflict
from yadori.domain.recall.model import RecallFailure
from yadori.infrastructure.settings import NotSettled, Settings, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation


@final
class CodexCompanion:
    """利用者が打つ入口。宿りの準備を確かめて Codex を同じ端末で待つ。"""

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
            print(f"（{settings.dweller.nickname} として Codex を起こします）")
            return CodexSession(settings.home, self._cwd).launch()
        except (NotSettled, EmbeddingsUnavailable, CodexSessionError, sqlite3.Error) as trouble:
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
class CodexHook:
    """一回の Codex だけが呼ぶ、思い出す・覚える・未完を捨てる入口。"""

    def __init__(
        self,
        event: str,
        run_dir: Path,
        home: Path | None = None,
        startup: Startup | None = None,
    ) -> None:
        self._event = event.lower().replace("_", "-")
        self._settings_file = SettingsFile(home)
        self._run_dir = run_dir
        self._continuity = Continuity(run_dir)
        self._words = CodexWords()
        self._pending_store = PendingStore(run_dir)
        self._startup = startup or Startup(home)

    def run(self) -> int:
        try:
            self._validate_run_dir()
            payload = self._payload()
            if self._event in {"user-prompt-submit", "userpromptsubmit"}:
                return self._user_prompt(payload)
            if self._event in {"stop"}:
                return self._stop(payload)
            if self._event in {"interrupt"}:
                return self._interrupt(payload)
            if self._event in {"session-start", "sessionstart"}:
                return self._session_start(payload)
            raise ValueError(f"知らない Codex のフック: {self._event}")
        except (
            OSError,
            ValueError,
            CannotSpeak,
            RememberingConflict,
            RuntimeError,
            RecallFailure,
            sqlite3.Error,
            NotSettled,
            EmbeddingsUnavailable,
        ) as trouble:
            print(f"宿りのフックを処理できません: {trouble}", file=sys.stderr)
            return 2

    def _user_prompt(self, payload: dict[str, object]) -> int:
        session_id = self._identifier(payload, "session_id")
        turn_id = self._turn_identifier(payload)
        utterance = self._text(payload, "prompt")
        old = self._pending_store.read(session_id)
        if old is not None and old.turn_id == turn_id:
            if old.utterance != utterance:
                raise ValueError("同じ turn_id へ異なる発話が届いた")
            print(old.context)
            return 0
        if old is not None and old.spoken is not None:
            try:
                self._remember(old)
            except (CannotSpeak, RememberingConflict, RuntimeError, sqlite3.Error) as trouble:
                print(f"前の一往復を覚えられません: {trouble}", file=sys.stderr)
                return 2
        self._pending_store.discard(session_id)
        previous = self._continuity.begin(session_id, f"codex:{turn_id}")
        settings, memories, conversation = self._conversation()
        try:
            recollection = conversation.recall(settings.dweller.id, utterance)
            turn = RecallLedger(self._run_dir, settings.dweller.id).begin(
                f"{session_id}:{turn_id}",
                [one.id for one in recollection.recent]
                + [one.episode.id for one in recollection.found],
            )
            context = self._words.hook_response(
                recollection, instructions=RecallGuidance.for_turn(turn)
            )
            self._pending_store.save(
                PendingTurn(
                    session_id,
                    turn_id,
                    utterance,
                    recollection.recalled_at,
                    recollection.identity.version,
                    context,
                    previous_source=previous,
                )
            )
        finally:
            memories.close()
        print(context)
        return 0

    def _stop(self, payload: dict[str, object]) -> int:
        if payload.get("subagent") is True:
            # 下位の担当（SubagentStop）は個別の一往復として保存しない。
            return 0
        session_id = self._identifier(payload, "session_id")
        turn_id = self._turn_identifier(payload)
        completed = self._pending_store.read_completed(turn_id)
        answer_text = self._reply_text(payload)
        if completed is not None:
            replayed = self._words.parted(answer_text)
            if replayed != completed:
                return self._block("同じ発話へ以前と異なる返事または気持ちが届きました")
            return 0
        pending = self._pending_store.read(session_id)
        if pending is None or pending.turn_id != turn_id:
            return 0
        try:
            spoken = self._words.parted(answer_text)
            pending = PendingTurn(
                pending.session_id,
                pending.turn_id,
                pending.utterance,
                pending.recalled_at,
                pending.identity_version,
                pending.context,
                spoken,
                pending.previous_source,
            )
            self._pending_store.save(pending)
            self._remember(pending)
            self._end_recall(session_id, turn_id)
        except (CannotSpeak, RememberingConflict, RuntimeError, sqlite3.Error) as trouble:
            return self._block(f"宿りがこの一往復を覚えられませんでした: {trouble}")
        try:
            self._pending_store.mark_completed(turn_id, spoken)
        except OSError as trouble:
            return self._block(f"完了した一往復の再送確認を残せませんでした: {trouble}")
        self._pending_store.discard(session_id)
        return 0

    def _interrupt(self, payload: dict[str, object]) -> int:
        session_id = str(payload.get("session_id", ""))
        if session_id:
            pending = self._pending_store.read(session_id)
            if pending is not None:
                self._end_recall(session_id, pending.turn_id)
            self._continuity.interrupt(session_id)
            self._pending_store.discard(session_id)
        return 0

    def _session_start(self, payload: dict[str, object]) -> int:
        reason = str(payload.get("reason", ""))
        if reason != "compact":
            return 0
        settings, memories, conversation = self._conversation()
        try:
            # 現在の名乗りと状態を取得するため、空文字列で思い出す
            recollection = conversation.recall(settings.dweller.id, "")
            response = self._words.compact_response(recollection)
            print(response)
            return 0
        finally:
            memories.close()

    def _block(self, reason: str) -> int:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    def _end_recall(self, session_id: str, turn_id: str) -> None:
        settings = self._settings_file.read()
        RecallLedger(self._run_dir, settings.dweller.id).end(f"{session_id}:{turn_id}")

    def _remember(self, pending: PendingTurn) -> None:
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
                source=f"codex:{pending.turn_id}",
                identity_version=pending.identity_version,
                session_id=f"codex:{pending.session_id}",
                previous_source=pending.previous_source,
            )
            self._continuity.finish(pending.session_id, f"codex:{pending.turn_id}")
        finally:
            memories.close()

    def _conversation(self) -> tuple[Settings, SqliteMemories, Conversation]:
        settings = self._settings_file.read()
        _ = RecallLedger(self._run_dir, settings.dweller.id)
        memories = SqliteMemories(settings.memories_path)
        try:
            self._startup.settle(memories, settings)
            conversation = self._startup.conversation(memories, settings)
            _ = conversation.rebuild_index(settings.dweller.id)
        except BaseException:
            memories.close()
            raise
        return settings, memories, conversation

    def _validate_run_dir(self) -> None:
        if not self._run_dir.exists():
            raise ValueError(f"専用ディレクトリが見つからない: {self._run_dir}")
        lock = self._run_dir / "session.lock"
        if not lock.exists():
            raise ValueError(f"施錠ファイルが無い: {lock}")

    def _payload(self) -> dict[str, object]:
        content = sys.stdin.read().strip()
        if not content:
            return {}
        try:
            parsed: object = json.loads(content)  # pyright: ignore[reportAny]
        except json.JSONDecodeError as trouble:
            raise ValueError("フックの入力を JSON として読めない") from trouble
        if not isinstance(parsed, dict):
            raise ValueError("フックの入力が物の組ではない")
        checked: dict[str, object] = {}
        for key, value in parsed.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise ValueError("フックの入力の鍵が文字列ではない")
            checked[key] = value
        return checked

    def _identifier(self, payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"フックの入力に {key} が無い")
        return value.strip()

    def _turn_identifier(self, payload: dict[str, object]) -> str:
        turn_id = payload.get("turn_id")
        if isinstance(turn_id, str) and turn_id.strip():
            return turn_id.strip()
        prompt_id = payload.get("prompt_id")
        if isinstance(prompt_id, str) and prompt_id.strip():
            return prompt_id.strip()
        raise ValueError("フックの入力に turn_id または prompt_id が無い")

    def _text(self, payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"フックの入力に {key} が無い")
        return value.strip()

    def _reply_text(self, payload: dict[str, object]) -> str:
        for key in ("last_assistant_message", "response", "reply"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise ValueError("フックの入力に返事本文が無い")

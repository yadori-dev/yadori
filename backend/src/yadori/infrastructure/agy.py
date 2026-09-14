"""agy の起動と通知を、宿りの会話の口へ繋ぐ。"""

from __future__ import annotations

import fcntl
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.adapter.recall.connection import NAME, TOOLS, RecallGuidance
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool.agy_notice import AgyJson, AgyNotice
from yadori.adapter.tool.agy_session import AgySession
from yadori.adapter.tool.agy_words import AgyWords
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import EmbeddingsUnavailable, RememberingConflict
from yadori.domain.recall.model import RecallFailure
from yadori.infrastructure.settings import Configuration, NotSettled, SettingsFile
from yadori.infrastructure.start import Startup


@final
class AgyMemory:
    """道具の記録を確定する前に回復用の原文を残す。"""

    def __init__(self, home: Path | None = None, startup: Startup | None = None) -> None:
        self._files = SettingsFile(home)
        self._startup = startup or Startup(home)
        self._words = AgyWords()

    def _optional_source(self, value: object) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("会話の所属または出典が文字列ではない")
        return value

    def prepare(self) -> Path:
        settings = self._files.read()
        memories = SqliteMemories(settings.memories_path)
        try:
            self._startup.settle(memories, settings)
            _ = self._startup.conversation(memories, settings).rebuild_index(settings.dweller.id)
        finally:
            memories.close()
        return settings.home

    def recall(self, notice: AgyNotice, path: Path, ledger: RecallLedger | None = None) -> str:
        if path.exists():
            record = AgyJson.object(path.read_text(encoding="utf-8"))
            if record.get("source") != notice.source or record.get("utterance") != notice.utterance:
                raise ValueError("同じ agy の発話へ異なる原文が届きました")
            return AgyJson.text(record, "context")
        settings = self._files.read()
        if ledger is not None and ledger.dweller_id != settings.dweller.id:
            raise RecallFailure("unavailable", "起動時と異なる人物へ切り替えることはできません")
        memories = SqliteMemories(settings.memories_path)
        try:
            self._startup.settle(memories, settings)
            recalled = self._startup.conversation(memories, settings).recall(
                settings.dweller.id, notice.utterance
            )
            instructions = ""
            if ledger is not None:
                token = ledger.begin(
                    notice.source,
                    [one.id for one in recalled.recent]
                    + [one.episode.id for one in recalled.found],
                )
                instructions = RecallGuidance.for_turn(token)
            context = self._words.hook_response(recalled, instructions=instructions)
            AgyJson.write(
                path,
                {
                    "dweller_id": settings.dweller.id,
                    "source": notice.source,
                    "session_id": f"agy:{notice.conversation_id}",
                    "previous_source": notice.previous_source,
                    "utterance": notice.utterance,
                    "recalled_at": recalled.recalled_at.isoformat(),
                    "identity_version": recalled.identity.version,
                    "context": context,
                    "reply": None,
                    "saved": False,
                },
            )
            return context
        finally:
            memories.close()

    def finish(self, notice: AgyNotice, path: Path) -> None:
        if not notice.finished:
            return
        record = AgyJson.object(path.read_text(encoding="utf-8"))
        if record.get("source") != notice.source or record.get("utterance") != notice.utterance:
            raise ValueError("agy の発話と返事が対応していません")
        old = record.get("reply")
        if old is not None and old != notice.reply:
            raise ValueError("同じ agy の発話へ異なる返事が届きました")
        record["reply"] = notice.reply
        AgyJson.write(path, record)
        self.replay(path)

    def replay(self, path: Path) -> None:
        record = AgyJson.object(path.read_text(encoding="utf-8"))
        if record.get("reply") is None:
            if record.get("saved") is not False:
                raise ValueError("agy の未完の回復記録を読めません")
            _ = AgyJson.text(record, "source")
            _ = AgyJson.text(record, "utterance")
            _ = AgyJson.text(record, "context")
            return
        spoken = self._words.parted(AgyJson.text(record, "reply"))
        settings = self._files.read()
        memories = SqliteMemories(settings.memories_path)
        try:
            if "dweller_id" not in record and len(Configuration(settings.home).load().people()) > 1:
                raise ValueError("旧い回復記録の人物を特定できません。原文を残して起動を止めます")
            identifier = record.get("dweller_id", settings.dweller.id)
            if not isinstance(identifier, str) or memories.dweller(identifier) is None:
                raise ValueError("回復記録の人物を確認できません。別の人物へは保存しません")
            _ = self._startup.conversation(memories, settings).remember(
                identifier,
                AgyJson.text(record, "utterance"),
                spoken.reply,
                spoken.moved,
                recalled_at=datetime.fromisoformat(AgyJson.text(record, "recalled_at")),
                source=AgyJson.text(record, "source"),
                session_id=self._optional_source(record.get("session_id")),
                previous_source=self._optional_source(record.get("previous_source")),
                identity_version=AgyJson.number(record, "identity_version"),
            )
            record["saved"] = True
            AgyJson.write(path, record)
        finally:
            memories.close()

    def recover(self, sessions: Path) -> None:
        if not sessions.exists():
            return
        for run_dir in sessions.iterdir():
            if not run_dir.is_dir() or run_dir.is_symlink():
                raise ValueError("agy の回復領域に予期しない項目があります")
            with (run_dir / "session.lock").open("r+") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                for record in sorted((run_dir / "turns").glob("*.json")):
                    self.replay(record)
                # 施錠されず残った記録は異常終了。完成状態を推定して捨てない。
                files = list((run_dir / "turns").glob("*.json"))
                unfinished = any(
                    AgyJson.object(path.read_text()).get("reply") is None for path in files
                )
                stopped_before_model = (
                    (run_dir / "failed-pre").exists()
                    and not (run_dir / "failed-stop").exists()
                    and not (run_dir / "failed-tool").exists()
                )
                if unfinished and not stopped_before_model:
                    raise ValueError(
                        "agy の完成状態を確認できません。回復用の記録を確認してください: "
                        + str(run_dir)
                    )
                shutil.rmtree(run_dir)


@final
class AgyHook:
    def __init__(
        self,
        event: str,
        run_dir: Path,
        home: Path | None = None,
        startup: Startup | None = None,
    ) -> None:
        self._event = event
        self._run_dir = run_dir
        self._memory = AgyMemory(home, startup)
        self._files = SettingsFile(home)

    def run(self) -> int:
        try:
            if self._event not in {"pre", "stop", "tool"}:
                raise ValueError("知らない agy の通知です")
            if not (self._run_dir / "session.lock").is_file():
                raise ValueError("agy の専用の起動がありません")
            _ = (self._run_dir / "stage").write_text(self._event)
            text = sys.stdin.read()
            notice = AgyNotice.read(self._event, text, self._run_dir)
            self._check_primary(notice)
            path = self._run_dir / "turns" / f"{notice.step}.json"
            ledger = RecallLedger(self._run_dir, self._files.read().dweller.id)
            if self._event == "tool":
                print(self._tool(text))
            elif self._event == "pre":
                print(self._memory.recall(notice, path, ledger))
            else:
                self._memory.finish(notice, path)
                if notice.finished:
                    ledger.end(notice.source)
                print("{}")
            return 0
        except (
            subprocess.SubprocessError,
            OSError,
            ValueError,
            RuntimeError,
            RecallFailure,
            CannotSpeak,
            RememberingConflict,
            sqlite3.Error,
            NotSettled,
            EmbeddingsUnavailable,
        ) as trouble:
            print(f"宿りを続けられません: {trouble}", file=sys.stderr)
            return 2

    def _tool(self, text: str) -> str:
        payload = AgyJson.object(text)
        call = AgyJson.object(json.dumps(payload.get("toolCall")))
        name = AgyJson.text(call, "name")
        allowed = {
            "view_file",
            "list_dir",
            "find_by_name",
            "grep_search",
            "write_to_file",
            "replace_file_content",
            "multi_replace_file_content",
            "run_command",
        }
        arguments = AgyJson.object(json.dumps(call.get("args", {})))
        if (
            name == "call_mcp_tool"
            and arguments.get("ServerName") == NAME
            and arguments.get("ToolName") in TOOLS
        ):
            return json.dumps({"decision": "allow"})
        if name not in allowed or arguments.get("RunPersistent") is True:
            return json.dumps(
                {
                    "decision": "deny",
                    "reason": "宿りの agy 起動では、検索・ファイル編集・端末操作だけを使えます。",
                },
                ensure_ascii=False,
            )
        readonly = {
            "view_file": "AbsolutePath",
            "list_dir": "DirectoryPath",
            "find_by_name": "SearchDirectory",
            "grep_search": "SearchPath",
        }
        key = readonly.get(name)
        target = arguments.get(key) if key is not None else None
        workspaces = payload.get("workspacePaths")
        if isinstance(target, str) and Path(target).is_absolute() and isinstance(workspaces, list):
            if any(
                isinstance(root, str)
                and Path(target).resolve().is_relative_to(Path(root).resolve())
                for root in workspaces  # pyright: ignore[reportUnknownVariableType]
            ):
                return json.dumps({"decision": "allow"})
        return json.dumps({"decision": "ask"})

    def _check_primary(self, notice: AgyNotice) -> None:
        path = self._run_dir / "primary"
        if path.exists():
            if path.read_text() != notice.conversation_id:
                raise ValueError("宿りの起動中に別の会話・担当へ切り替えることはできません")
        else:
            _ = path.write_text(notice.conversation_id)


@final
class AgyCompanion:
    def run(self) -> int:
        try:
            memory = AgyMemory()
            home = memory.prepare()
            memory.recover(home / "agy/sessions")
            nickname = SettingsFile(home).read().dweller.nickname
            print(f"（{nickname} として agy を起こします）", flush=True)
            return AgySession(home).launch()
        except (
            subprocess.SubprocessError,
            OSError,
            ValueError,
            RuntimeError,
            CannotSpeak,
            RememberingConflict,
            sqlite3.Error,
            NotSettled,
            EmbeddingsUnavailable,
        ) as trouble:
            print(f"agy を起動できません: {trouble}", file=sys.stderr)
            return 1

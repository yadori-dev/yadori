"""普段の Codex に宿りのフックと記憶の接続だけを追加する。"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, final

from yadori.adapter.recall.connection import NAME, MemoryConnection, MemoryConnectionError
from yadori.adapter.tool.pending_turn import StaleSessionCleaner


class CodexSessionError(Exception):
    """Codex を起動できない。"""


@dataclass(frozen=True)
class PreparedCodex:
    """通常設定を使う起動と、宿りだけの一時状態。"""

    run_dir: Path
    argv: tuple[str, ...]
    environment: dict[str, str]
    lock: IO[str]


@final
class CodexSession:
    """信頼確認、設定の合成、認証、履歴は Codex に任せる。"""

    def __init__(
        self,
        home: Path,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        executable: str = "codex",
    ) -> None:
        self._home = home.resolve()
        self._cwd = (cwd or Path.cwd()).resolve()
        self._environment = dict(os.environ if environment is None else environment)
        self._executable = executable

    def launch(self, arguments: Sequence[str] = ()) -> int:
        prepared = self.prepare()
        try:
            return subprocess.call(
                [*prepared.argv, *arguments], cwd=self._cwd, env=prepared.environment
            )
        except OSError as trouble:
            raise CodexSessionError(f"Codex を起動できない: {trouble}") from trouble
        finally:
            self.finish(prepared)

    def prepare(self) -> PreparedCodex:
        try:
            MemoryConnection.check_regular("codex", self._cwd, self._environment)
        except MemoryConnectionError as trouble:
            raise CodexSessionError(str(trouble)) from trouble
        if shutil.which(self._executable, path=self._environment.get("PATH")) is None:
            raise CodexSessionError("Codex が見つからない。先に codex を入れてください")
        sessions = self._home / "codex" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        StaleSessionCleaner.clean(sessions)
        run_dir = sessions / uuid.uuid4().hex
        run_dir.mkdir(mode=0o700)
        lock = (run_dir / "session.lock").open("w", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            definitions = self._hooks()
            connection = MemoryConnection.codex(run_dir, self._home, self._environment)
            argv = [self._executable]
            for event, groups in definitions.items():
                argv.extend(["-c", f"hooks.{event}={self._literal(groups)}"])
            argv.extend(["-c", f"mcp_servers.{NAME}={self._literal(connection)}"])
            environment = dict(self._environment)
            environment["YADORI_HOME"] = str(self._home)
            environment["YADORI_RUN_DIR"] = str(run_dir)
            return PreparedCodex(run_dir, tuple(argv), environment, lock)
        except BaseException:
            lock.close()
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    def finish(self, prepared: PreparedCodex) -> None:
        prepared.lock.close()
        try:
            shutil.rmtree(prepared.run_dir)
        except OSError as trouble:
            print(f"警告: 宿りの一時状態を片付けられませんでした: {trouble}", file=sys.stderr)

    def _hooks(self) -> dict[str, object]:
        entry = Path(sys.executable).with_name("yadori").resolve()
        if not entry.is_file():
            raise CodexSessionError("yadori の命令が導入先にありません。入れ直してください")
        definitions: dict[str, object] = {}
        for event, subcommand in (
            ("UserPromptSubmit", "user-prompt-submit"),
            ("Stop", "stop"),
            ("Interrupt", "interrupt"),
            ("SessionStart", "session-start"),
        ):
            command = shlex.join([str(entry), "_codex-hook", subcommand])
            # 場所を環境で渡すことで、確認した命令の内容を起動ごとに変えない。
            command += ' "$YADORI_RUN_DIR"'
            handler: dict[str, object] = {"type": "command", "command": command, "timeout": 10}
            if event in {"UserPromptSubmit", "SessionStart"}:
                handler["additionalContextLimit"] = 0
            definitions[event] = [{"hooks": [handler]}]
        return definitions

    @staticmethod
    def _object(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise CodexSessionError("追加する設定を読めません")
        result: dict[str, object] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise CodexSessionError("追加する設定名が文字列ではありません")
            result[key] = item
        return result

    @classmethod
    def _literal(cls, value: object) -> str:
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(
                    json.dumps(key) + " = " + cls._literal(item)
                    for key, item in cls._object(value).items()  # pyright: ignore[reportUnknownArgumentType]
                )
                + "}"
            )
        if isinstance(value, list):
            return "[" + ", ".join(cls._literal(item) for item in value) + "]"  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if isinstance(value, (str, bool, int)):
            return json.dumps(value, ensure_ascii=False)
        raise CodexSessionError("追加する設定値の型に対応していません")

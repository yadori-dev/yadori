"""普段の Claude Code に宿りのフックと記憶の接続だけを追加する。"""

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


class ClaudeSessionError(Exception):
    """Claude Code を起動できない。"""


@dataclass(frozen=True)
class PreparedClaude:
    """通常設定を使う起動と、宿りだけの一時状態。"""

    run_dir: Path
    argv: tuple[str, ...]
    environment: dict[str, str]
    lock: IO[str]


@final
class ClaudeSession:
    """信頼確認、設定の合成、認証、履歴は Claude Code に任せる。"""

    def __init__(
        self,
        home: Path,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        executable: str = "claude",
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
            raise ClaudeSessionError(f"Claude Code を起動できない: {trouble}") from trouble
        finally:
            self.finish(prepared)

    def prepare(self) -> PreparedClaude:
        try:
            MemoryConnection.check_regular("claude", self._cwd, self._environment)
        except MemoryConnectionError as trouble:
            raise ClaudeSessionError(str(trouble)) from trouble
        if shutil.which(self._executable, path=self._environment.get("PATH")) is None:
            raise ClaudeSessionError("Claude Code が見つからない。先に claude を入れてください")
        sessions = self._home / "claude" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        StaleSessionCleaner.clean(sessions)
        run_dir = sessions / uuid.uuid4().hex
        run_dir.mkdir(mode=0o700)
        lock = (run_dir / "session.lock").open("w", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            settings = self._required_settings(run_dir)
            settings_path = run_dir / "yadori-settings.json"
            _ = settings_path.write_text(json.dumps(settings), encoding="utf-8")
            mcp_path = run_dir / "mcp.json"
            _ = mcp_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            NAME: MemoryConnection.command(run_dir, self._home, self._environment)
                        }
                    }
                ),
                encoding="utf-8",
            )
            environment = dict(self._environment)
            environment["YADORI_HOME"] = str(self._home)
            return PreparedClaude(
                run_dir,
                (self._executable, "--settings", str(settings_path), "--mcp-config", str(mcp_path)),
                environment,
                lock,
            )
        except BaseException:
            lock.close()
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    def finish(self, prepared: PreparedClaude) -> None:
        prepared.lock.close()
        try:
            shutil.rmtree(prepared.run_dir)
        except OSError as trouble:
            print(f"警告: 宿りの一時状態を片付けられませんでした: {trouble}", file=sys.stderr)

    def _required_settings(self, run_dir: Path) -> dict[str, object]:
        entry = Path(sys.executable).with_name("yadori").resolve()
        if not entry.is_file():
            raise ClaudeSessionError(
                "yadori の命令が導入先にありません。"
                + "uv tool install --editable . で入れ直してください"
            )
        command = [str(entry), "_claude-hook"]
        hooks: dict[str, object] = {}
        for event in ("UserPromptSubmit", "Stop", "StopFailure", "SessionEnd"):
            joined = " ".join(shlex.quote(part) for part in [*command, event, str(run_dir)])
            hooks[event] = [{"hooks": [{"type": "command", "command": joined, "timeout": 180}]}]
        return {"hooks": hooks}

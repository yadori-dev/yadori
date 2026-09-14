"""普段の Codex から安全な道具だけを借り、宿り専用の起動を作る。"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, ClassVar, final

import tomlkit

from yadori.adapter.recall.connection import NAME, MemoryConnection, MemoryConnectionError
from yadori.adapter.tool.pending_turn import StaleSessionCleaner


class CodexSessionError(Exception):
    """安全に Codex を起こせない。理由と直し方を持つ。"""


@dataclass(frozen=True)
class PreparedCodex:
    """一回の起動に閉じた設定、命令、環境、施錠。"""

    run_dir: Path
    argv: tuple[str, ...]
    environment: dict[str, str]
    lock: IO[str]
    credential: Path | None


@final
class CodexSession:
    """専用設定を作り、ChatGPT 認証を確かめ、同じ端末で Codex を待つ。"""

    _SCALARS = ("model", "model_reasoning_effort", "approval_policy", "sandbox_mode", "theme")
    _PROVIDER_ENV: ClassVar[set[str]] = {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CODEX_API_KEY",
    }
    _GIT_LOCAL_ENV: ClassVar[set[str]] = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }

    def __init__(
        self,
        home: Path,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        executable: str = "codex",
    ) -> None:
        self._home = home
        self._cwd = (cwd or Path.cwd()).resolve()
        self._environment = dict(environment or os.environ)
        self._executable = executable
        configured = self._environment.get("CODEX_CONFIG_DIR")
        if configured:
            written = Path(configured).expanduser()
            self._usual = (written if written.is_absolute() else self._cwd / written).resolve()
        else:
            self._usual = Path.home() / ".codex"

    def launch(self) -> int:
        prepared = self.prepare()
        try:
            self._verify_auth(prepared)
            return subprocess.call(prepared.argv, cwd=self._cwd, env=prepared.environment)
        except OSError as trouble:
            raise CodexSessionError(f"Codex を起動できない: {trouble}") from trouble
        finally:
            self.finish(prepared)

    def prepare(self) -> PreparedCodex:
        """古い置き場を片付け、安全な設定を一回ぶんへ写す。"""
        if shutil.which(self._executable) is None:
            raise CodexSessionError("Codex が見つからない。先に codex を入れてください")
        sessions = self._home / "codex" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        StaleSessionCleaner.clean(sessions)
        run_dir = sessions / uuid.uuid4().hex
        run_dir.mkdir(mode=0o700)
        lock = (run_dir / "session.lock").open("w", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            settings = self._borrowed(run_dir)
            settings["mcp_servers"] = {
                NAME: MemoryConnection.codex(run_dir, self._home, self._environment)
            }
            self._write_toml(run_dir / "config.toml", settings)
            self._write_hooks(run_dir)
            credential = self._link_credential(run_dir)
            environment = self._child_environment(run_dir)
            argv = (self._executable, "--dangerously-bypass-hook-trust")
            return PreparedCodex(run_dir, argv, environment, lock, credential)
        except BaseException as trouble:
            lock.close()
            shutil.rmtree(run_dir, ignore_errors=True)
            if isinstance(trouble, MemoryConnectionError):
                raise CodexSessionError(str(trouble)) from trouble
            raise

    def finish(self, prepared: PreparedCodex) -> None:
        """認証の参照が置き換わっていないかを確かめ、一回の履歴ごと捨てる。"""
        credential = prepared.run_dir / "auth.json"
        if prepared.credential is not None and (
            not credential.is_symlink() or credential.resolve() != prepared.credential.resolve()
        ):
            print(
                "警告: Codex が認証ファイルの参照を置き換えました。"
                + "普段の認証へは戻していません。",
                file=sys.stderr,
            )
        prepared.lock.close()
        try:
            shutil.rmtree(prepared.run_dir)
        except OSError as trouble:
            print(f"警告: Codex の一時履歴を捨てられませんでした: {trouble}", file=sys.stderr)

    def _borrowed(self, run_dir: Path) -> dict[str, object]:
        del run_dir
        user = self._toml_object(self._usual / "config.toml", missing_ok=True)
        root = self._workspace_root()
        trust_root = self._trust_root(root)
        project_config = self._cwd / ".codex" / "config.toml"
        project = self._toml_object(project_config, missing_ok=True)
        self._require_trust(root, trust_root, user, project)
        self._reject_provider_env((user, project))
        MemoryConnection.check_name(user.get("mcp_servers"))
        MemoryConnection.check_name(project.get("mcp_servers"))
        merged = self._merge_settings(user, project)
        # 危険モードを除外・安全化
        self._sanitize_danger(merged)
        # 作業場所の信頼レベルを設定
        projects = merged.setdefault("projects", {})
        if isinstance(projects, dict):
            projects[str(trust_root)] = {"trust_level": "trusted"}
        return merged

    def _merge_settings(
        self, user: dict[str, object], project: dict[str, object]
    ) -> dict[str, object]:
        merged: dict[str, object] = {}
        for key in self._SCALARS:
            if key in user:
                merged[key] = user[key]
            if key in project:
                merged[key] = project[key]
        return merged

    def _sanitize_danger(self, settings: dict[str, object]) -> None:
        """確認と隔離を飛ばす危険な設定を除外する。"""
        if settings.get("approval_policy") in {"never", "danger-full-access"}:
            _ = settings.pop("approval_policy", None)
        if settings.get("sandbox_mode") in {"danger-full-access", "disabled"}:
            _ = settings.pop("sandbox_mode", None)

    def _require_trust(
        self,
        root: Path,
        trust_root: Path,
        user: dict[str, object],
        project: dict[str, object],
    ) -> None:
        """作業場所の信頼を確かめる。未信頼なら起動を断る。"""
        assets = [
            root / ".codex" / "config.toml",
            root / ".codex" / "hooks.json",
        ]
        if not any(path.exists() for path in assets):
            return
        user_projects = self._mapping(user.get("projects"))
        if user_projects is not None:
            entry = self._mapping(user_projects.get(str(trust_root)))
            if entry is not None and entry.get("trust_level") == "trusted":
                return
        if project.get("trust_level") == "trusted":
            return
        raise CodexSessionError(
            f"作業場所 {self._cwd} は普段の Codex で信頼されていません。"
            + "先に通常の codex で開いて信頼を確認してください"
        )

    def _workspace_root(self) -> Path:
        clean = {k: v for k, v in self._environment.items() if k not in self._GIT_LOCAL_ENV}
        try:
            done = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                check=False,
                env=clean,
            )
            if done.returncode == 0 and done.stdout.strip():
                return Path(done.stdout.strip()).resolve()
        except OSError:
            pass
        return self._cwd

    def _trust_root(self, root: Path) -> Path:
        clean = {k: v for k, v in self._environment.items() if k not in self._GIT_LOCAL_ENV}
        try:
            done = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                env=clean,
            )
            if done.returncode == 0 and done.stdout.strip():
                common = Path(done.stdout.strip())
                common_resolved = (root / common).resolve() if not common.is_absolute() else common
                if common_resolved.name == ".git":
                    return common_resolved.parent
                return common_resolved
        except OSError:
            pass
        return root

    def _reject_provider_env(self, sources: tuple[dict[str, object], ...]) -> None:
        for key in self._PROVIDER_ENV:
            if key in self._environment:
                raise CodexSessionError(
                    f"環境変数 {key} で別の接続先が指定されています。"
                    + "ChatGPT 契約によるログインだけを使ってください"
                )
        for source in sources:
            for key in ("api_key", "openai_api_key", "base_url"):
                if key in source:
                    raise CodexSessionError(
                        f"設定で {key} が指定されています。"
                        + "ChatGPT 契約によるログインだけを使ってください"
                    )

    def _link_credential(self, run_dir: Path) -> Path | None:
        usual_auth = self._usual / "auth.json"
        if not usual_auth.exists():
            return None
        target = run_dir / "auth.json"
        try:
            target.symlink_to(usual_auth)
            return usual_auth
        except OSError:
            return None

    def _child_environment(self, run_dir: Path) -> dict[str, str]:
        env = {k: v for k, v in self._environment.items() if k not in self._GIT_LOCAL_ENV}
        for key in self._PROVIDER_ENV:
            _ = env.pop(key, None)
        env["CODEX_HOME"] = str(run_dir)
        env["CODEX_SQLITE_HOME"] = str(run_dir)
        return env

    def _write_hooks(self, run_dir: Path) -> None:
        entry = Path(sys.executable).with_name("yadori").resolve()
        if entry.is_file():
            base_cmd = [str(entry)]
        else:
            resolved = shutil.which("yadori")
            base_cmd = [resolved] if resolved else [sys.executable, "-m", "yadori"]

        def _hook_cmd(subcommand: str) -> str:
            parts = [*base_cmd, "_codex-hook", subcommand, str(run_dir)]
            return " ".join(shlex.quote(part) for part in parts)

        hooks_def: dict[str, object] = {
            "description": "yadori codex companion hooks",
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _hook_cmd("user-prompt-submit"),
                                "timeout": 10,
                                "additionalContextLimit": 0,
                            }
                        ]
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _hook_cmd("stop"),
                                "timeout": 10,
                            }
                        ]
                    }
                ],
                "Interrupt": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _hook_cmd("interrupt"),
                                "timeout": 10,
                            }
                        ]
                    }
                ],
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _hook_cmd("session-start"),
                                "timeout": 10,
                                "additionalContextLimit": 0,
                            }
                        ]
                    }
                ],
            },
        }
        _ = (run_dir / "hooks.json").write_text(
            json.dumps(hooks_def, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _write_toml(self, path: Path, data: dict[str, object]) -> None:
        _ = path.write_text(tomlkit.dumps(data), encoding="utf-8")

    def _verify_auth(self, prepared: PreparedCodex) -> None:
        try:
            done = subprocess.run(
                [self._executable, "login", "status"],
                cwd=self._cwd,
                env=prepared.environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as trouble:
            raise CodexSessionError(f"Codex の認証状態を確認できません: {trouble}") from trouble
        output = (done.stdout + "\n" + done.stderr).strip()
        if done.returncode != 0 or "ChatGPT" not in output:
            raise CodexSessionError(
                f"ChatGPT 契約によるログインを確認できません（{output[:200]}）。"
                + "普段の codex login でログインしてください"
            )

    def _mapping(self, value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

    def _toml_object(self, path: Path, missing_ok: bool = False) -> dict[str, object]:
        if not path.exists():
            if missing_ok:
                return {}
            raise CodexSessionError(f"設定ファイルが見つかりません: {path}")
        try:
            with path.open("rb") as opened:
                loaded: dict[str, object] = tomllib.load(opened)
                return {str(k): v for k, v in loaded.items()}
        except (tomllib.TOMLDecodeError, OSError) as broken:
            raise CodexSessionError(f"設定ファイルを読めません: {path}: {broken}") from broken

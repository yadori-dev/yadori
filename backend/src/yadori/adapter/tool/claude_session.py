"""普段の Claude Code から安全な道具だけを借り、宿り専用の起動を作る。"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, ClassVar, final

from yadori.adapter.recall.connection import NAME, MemoryConnection, MemoryConnectionError


class ClaudeSessionError(Exception):
    """安全に Claude Code を起こせない。理由と直し方を持つ。"""


@dataclass(frozen=True)
class PreparedClaude:
    """一回の起動に閉じた設定、命令、環境、施錠。"""

    run_dir: Path
    argv: tuple[str, ...]
    environment: dict[str, str]
    lock: IO[str]
    credential: Path | None


@final
class ClaudeSession:
    """専用設定を作り、認証を確かめ、同じ端末で Claude Code を待つ。"""

    _SCALARS = ("model", "effortLevel", "theme", "tui", "statusLine")
    _MAPS = ("enabledPlugins", "extraKnownMarketplaces", "modelSettings")
    _PROVIDER_ENV: ClassVar[set[str]] = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_VERTEX",
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
        executable: str = "claude",
    ) -> None:
        self._home = home
        self._cwd = (cwd or Path.cwd()).resolve()
        self._environment = dict(environment or os.environ)
        self._executable = executable
        configured = self._environment.get("CLAUDE_CONFIG_DIR")
        if configured:
            written = Path(configured).expanduser()
            self._usual = (written if written.is_absolute() else self._cwd / written).resolve()
        else:
            self._usual = Path.home() / ".claude"
        self._state = (
            self._usual / ".claude.json" if configured else self._usual.parent / ".claude.json"
        )

    def launch(self) -> int:
        prepared = self.prepare()
        try:
            self._verify_auth(prepared)
            return subprocess.call(prepared.argv, cwd=self._cwd, env=prepared.environment)
        except OSError as trouble:
            raise ClaudeSessionError(f"Claude Code を起動できない: {trouble}") from trouble
        finally:
            self.finish(prepared)

    def prepare(self) -> PreparedClaude:
        """古い置き場を片付け、安全な設定を一回ぶんへ写す。"""
        if shutil.which(self._executable) is None:
            raise ClaudeSessionError("Claude Code が見つからない。先に claude を入れてください")
        sessions = self._home / "claude" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._clean_old(sessions)
        run_dir = sessions / uuid.uuid4().hex
        run_dir.mkdir(mode=0o700)
        lock = (run_dir / "session.lock").open("w", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            settings, project = self._borrowed(run_dir)
            MemoryConnection.claude_permissions(settings)
            self._write_json(run_dir / "settings.json", settings)
            state = self._json_object(self._state, missing_ok=True)
            copied: dict[str, object] = {}
            if isinstance(state.get("hasCompletedOnboarding"), bool):
                copied["hasCompletedOnboarding"] = state["hasCompletedOnboarding"]
            for key in ("lastOnboardingVersion", "theme"):
                if isinstance(state.get(key), str):
                    copied[key] = state[key]
            copied["projects"] = project
            self._write_json(run_dir / ".claude.json", copied)
            required = self._required_settings(run_dir)
            required_path = run_dir / "yadori-settings.json"
            self._write_json(required_path, required)
            mcp_path = run_dir / "mcp.json"
            self._write_json(mcp_path, self._mcp(run_dir))
            credential = self._link_credential(run_dir)
            environment = self._child_environment(run_dir)
            argv = (
                self._executable,
                "--setting-sources",
                "user",
                "--settings",
                str(required_path),
                "--mcp-config",
                str(mcp_path),
                "--strict-mcp-config",
            )
            return PreparedClaude(run_dir, argv, environment, lock, credential)
        except BaseException as trouble:
            lock.close()
            shutil.rmtree(run_dir, ignore_errors=True)
            if isinstance(trouble, MemoryConnectionError):
                raise ClaudeSessionError(str(trouble)) from trouble
            raise

    def finish(self, prepared: PreparedClaude) -> None:
        """認証の参照が置き換わっていないかを確かめ、一回の履歴ごと捨てる。"""
        credential = prepared.run_dir / ".credentials.json"
        if prepared.credential is not None and (
            not credential.is_symlink() or credential.resolve() != prepared.credential.resolve()
        ):
            print(
                "警告: Claude Code が認証ファイルの参照を置き換えました。"
                + "普段の認証へは戻していません。",
                file=sys.stderr,
            )
        prepared.lock.close()
        try:
            shutil.rmtree(prepared.run_dir)
        except OSError as trouble:
            print(f"警告: Claude Code の一時履歴を捨てられませんでした: {trouble}", file=sys.stderr)

    def _borrowed(self, run_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
        user = self._json_object(self._usual / "settings.json", missing_ok=True)
        root = self._workspace_root()
        trust_root = self._trust_root(root)
        project_path = self._cwd / ".claude" / "settings.json"
        local_paths = self._local_paths(trust_root)
        project = self._json_object(project_path, missing_ok=True)
        locals_read = tuple(self._json_object(path, missing_ok=True) for path in local_paths)
        state = self._json_object(self._state, missing_ok=True)
        project_state = self._project_state(state, trust_root)
        self._require_trust(root, (project_path, *local_paths), project_state)
        borrowed = (user, project, *locals_read)
        self._reject_provider_env(borrowed)
        merged = self._merge_settings(
            (
                (user, self._usual),
                (project, self._cwd),
                *((local, self._cwd) for local in locals_read),
            )
        )
        self._copy_words(run_dir, root)
        self._filter_plugins(merged, borrowed)
        kept_values: dict[str, object] = {
            "hasTrustDialogAccepted": True,
            "enabledMcpjsonServers": self._strings(project_state.get("enabledMcpjsonServers")),
            "disabledMcpjsonServers": self._strings(project_state.get("disabledMcpjsonServers")),
            "disabledMcpServers": self._strings(project_state.get("disabledMcpServers")),
        }
        for key in ("allowedTools", "mcpContextUris"):
            if key in project_state:
                kept_values[key] = project_state[key]
        kept_project: dict[str, object] = {str(trust_root): kept_values}
        return merged, kept_project

    def _merge_settings(
        self, sources: tuple[tuple[dict[str, object], Path], ...]
    ) -> dict[str, object]:
        merged: dict[str, object] = {}
        permissions: dict[str, list[str]] = {key: [] for key in ("allow", "ask", "deny")}
        directories: list[str] = []
        for source, base in sources:
            for key in self._SCALARS:
                if key in source:
                    merged[key] = source[key]
            for key in self._MAPS:
                value = self._mapping(source.get(key))
                if value is not None:
                    current = self._mapping(merged.setdefault(key, {})) or {}
                    current.update(value)
                    merged[key] = current
            raw_permissions = self._mapping(source.get("permissions"))
            if raw_permissions is None:
                continue
            for key in permissions:
                for rule in self._strings(raw_permissions.get(key)):
                    normalized = self._permission(rule, base)
                    if normalized not in permissions[key]:
                        permissions[key].append(normalized)
            for directory in self._strings(raw_permissions.get("additionalDirectories")):
                normalized = self._directory(directory, base)
                if normalized not in directories:
                    directories.append(normalized)
        permissions["additionalDirectories"] = directories
        merged["permissions"] = permissions
        return merged

    def _permission(self, rule: str, base: Path) -> str:
        opening = rule.find("(")
        if opening < 0 or not rule.endswith(")"):
            return rule
        tool, specifier = rule[:opening], rule[opening + 1 : -1]
        if tool not in {"Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit"}:
            return rule
        if not specifier.startswith("/") or specifier.startswith("//"):
            return rule
        absolute = base.resolve().as_posix().rstrip("/") + "/" + specifier[1:]
        return f"{tool}(//{absolute.lstrip('/')})"

    def _directory(self, written: str, base: Path) -> str:
        expanded = Path(written).expanduser()
        return str(expanded if expanded.is_absolute() else (base / expanded).absolute())

    def _require_trust(
        self,
        root: Path,
        settings_paths: tuple[Path, ...],
        project_state: dict[str, object],
    ) -> None:
        assets = [*settings_paths, root / ".mcp.json"]
        assets.extend(
            source / name
            for source in self._word_sources(root)
            for name in ("agents", "commands", "skills")
        )
        if not any(path.exists() for path in assets):
            return
        if project_state.get("hasTrustDialogAccepted") is True:
            return
        raise ClaudeSessionError(
            f"{root} の作業設定はまだ信頼されていません。"
            + "先に同じ場所で普段の claude を起動し、内容を読んで信頼するか判断してください"
        )

    def _local_paths(self, trust_root: Path) -> tuple[Path, ...]:
        current = self._cwd / ".claude" / "settings.local.json"
        shared = trust_root / ".claude" / "settings.local.json"
        return (shared,) if current == shared else (current, shared)

    def _mcp(self, run_dir: Path) -> dict[str, object]:
        state = self._json_object(self._state, missing_ok=True)
        root = self._workspace_root()
        project_state = self._project_state(state, self._trust_root(root))
        disabled_regular = set(self._strings(project_state.get("disabledMcpServers")))
        if NAME in disabled_regular or NAME in self._strings(
            project_state.get("disabledMcpjsonServers")
        ):
            raise MemoryConnectionError(
                "記憶サーバーyadori_memoryが無効化されています。既存の指定を確認してください"
            )
        servers: dict[str, object] = {}
        MemoryConnection.check_name(state.get("mcpServers"))
        self._add_mcp(servers, state.get("mcpServers"), "利用者", disabled_regular)
        local_servers = project_state.get("mcpServers")
        MemoryConnection.check_name(local_servers)
        project_file = self._json_object(root / ".mcp.json", missing_ok=True)
        declared = self._mapping(project_file.get("mcpServers"))
        MemoryConnection.check_name(declared)
        if "mcpServers" in project_file and declared is None:
            raise ClaudeSessionError("作業場所の MCP 設定の形が違います")
        if declared is not None:
            enabled = set(self._strings(project_state.get("enabledMcpjsonServers")))
            disabled_project = set(self._strings(project_state.get("disabledMcpjsonServers")))
            pending = {
                str(name)
                for name in declared
                if name not in enabled and name not in disabled_project
            }
            if pending:
                names = "、".join(sorted(pending))
                raise ClaudeSessionError(
                    f".mcp.json の {names} はまだ利用可否が決まっていません。"
                    + "先に普段の claude の /mcp で内容を確認してください"
                )
            chosen = {name: value for name, value in declared.items() if name in enabled}
            self._add_mcp(servers, chosen, "作業場所", disabled_regular)
        self._add_mcp(servers, local_servers, "手元だけ", disabled_regular)
        servers[NAME] = MemoryConnection.command(run_dir, self._home, self._environment)
        return {"mcpServers": servers}

    def _add_mcp(
        self, target: dict[str, object], value: object, source: str, disabled: set[str]
    ) -> None:
        if value is None:
            return
        servers = self._mapping(value)
        if servers is None:
            raise ClaudeSessionError(f"{source}の MCP 設定の形が違います")
        for name, server in servers.items():
            if name in disabled:
                continue
            checked = self._mapping(server)
            if checked is None:
                raise ClaudeSessionError(f"{source}の MCP 設定の形が違います")
            if "headersHelper" in checked:
                print(
                    f"警告: {source}の MCP {name} は headersHelper を持つため借りません",
                    file=sys.stderr,
                )
                continue
            target[name] = checked

    def _filter_plugins(
        self, settings: dict[str, object], sources: tuple[dict[str, object], ...]
    ) -> None:
        enabled = self._mapping(settings.get("enabledPlugins"))
        if enabled is None:
            return
        installed = self._json_object(
            self._usual / "plugins" / "installed_plugins.json", missing_ok=True
        ).get("plugins")
        configured: set[str] = set()
        for source in sources:
            configs = self._mapping(source.get("pluginConfigs"))
            if configs is not None:
                configured.update(configs)
        kept: dict[str, object] = {}
        for name, active in enabled.items():
            if active is not True:
                continue
            path = self._installed_plugin(installed, name)
            reason = self._plugin_reason(name, path, configured)
            if reason is None:
                kept[name] = True
            else:
                print(f"警告: プラグイン {name} は{reason}ため借りません", file=sys.stderr)
        settings["enabledPlugins"] = kept

    def _installed_plugin(self, installed: object, name: str) -> Path | None:
        installed_map = self._mapping(installed)
        if installed_map is None:
            return None
        records = self._sequence(installed_map.get(name))
        if not records:
            return None
        for record in reversed(records):
            checked = self._mapping(record)
            if checked is not None:
                path = checked.get("installPath")
                if isinstance(path, str):
                    return Path(path)
        return None

    def _plugin_reason(self, name: str, path: Path | None, configured: set[str]) -> str | None:
        if path is None or not path.is_dir():
            return "導入済みの本体が見つからない"
        if name in configured:
            return "利用者別の設定を必要とする"
        manifest_reason = self._manifest_reason(name, path / ".claude-plugin" / "plugin.json")
        if manifest_reason is not None:
            return manifest_reason
        data_name = "".join(
            character if character.isalnum() or character in "_-" else "-" for character in name
        )
        if (self._usual / "plugins" / "data" / data_name).exists():
            return "永続データを持つ"
        for relative in ("hooks/hooks.json", "monitors/monitors.json"):
            if (path / relative).exists():
                return "常時動く処理を持つ"
        if (path / "output-styles").exists():
            return "話し方の指示を持つ"
        for candidate in path.rglob("*"):
            if candidate.suffix not in {".json", ".md", ".sh", ".py", ".js", ".ts", ".toml"}:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return "内容を安全に確認できない"
            if "${CLAUDE_PLUGIN_DATA}" in text or "${user_config." in text:
                return "利用者別の設定か永続データを必要とする"
        if (path / ".mcp.json").exists():
            print(f"警告: プラグイン {name} 自身の MCP は借りません", file=sys.stderr)
        return None

    def _manifest_reason(self, name: str, path: Path) -> str | None:
        if not path.is_file():
            return "構成を確認できない"
        try:
            parsed: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return "構成を安全に読めない"
        manifest = self._mapping(parsed)
        if manifest is None:
            return "構成の形が違う"
        unsafe = {
            "hooks": "常時動く処理を持つ",
            "outputStyles": "話し方の指示を持つ",
        }
        for key, reason in unsafe.items():
            if key in manifest:
                return reason
        experimental = self._mapping(manifest.get("experimental"))
        if experimental is not None and "monitors" in experimental:
            return "監視を持つ"
        if "mcpServers" in manifest:
            print(f"警告: プラグイン {name} 自身の MCP は借りません", file=sys.stderr)
        return None

    def _copy_words(self, run_dir: Path, root: Path) -> None:
        for source in (self._usual, *self._word_sources(root)):
            for name in ("agents", "commands", "skills"):
                path = source / name
                if path.is_dir():
                    _ = shutil.copytree(path, run_dir / name, dirs_exist_ok=True)

    def _word_sources(self, root: Path) -> tuple[Path, ...]:
        nested: list[Path] = []
        for candidate in (self._cwd, *self._cwd.parents):
            nested.append(candidate / ".claude")
            if candidate == root:
                break
        return tuple(reversed(nested))

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
            joined = " ".join(self._quoted(part) for part in [*command, event, str(run_dir)])
            hooks[event] = [{"hooks": [{"type": "command", "command": joined, "timeout": 180}]}]
        return {
            "hooks": hooks,
            "disableAllHooks": False,
            "autoMemoryEnabled": False,
            "disableClaudeAiConnectors": True,
            "claudeMdExcludes": ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"],
        }

    def _child_environment(self, run_dir: Path) -> dict[str, str]:
        child = {
            key: value for key, value in self._environment.items() if key not in self._PROVIDER_ENV
        }
        child.update(
            {
                "CLAUDE_CONFIG_DIR": str(run_dir),
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                "CLAUDE_CODE_DISABLE_CRON": "1",
                "CLAUDE_CODE_PLUGIN_SEED_DIR": str(self._usual / "plugins"),
            }
        )
        return child

    def _reject_provider_env(self, sources: tuple[dict[str, object], ...]) -> None:
        for source in sources:
            env = self._mapping(source.get("env"))
            if env is not None and any(key in self._PROVIDER_ENV for key in env):
                raise ClaudeSessionError(
                    "Claude Code の設定に従量課金または別の接続先を選ぶ環境変数があります。"
                    + "普段の設定から外してから起動してください"
                )

    def _link_credential(self, run_dir: Path) -> Path | None:
        source = self._usual / ".credentials.json"
        if not source.exists():
            return None
        target = run_dir / ".credentials.json"
        target.symlink_to(source)
        return source

    def _verify_auth(self, prepared: PreparedClaude) -> None:
        try:
            done = subprocess.run(
                [self._executable, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                cwd=self._cwd,
                env=prepared.environment,
            )
        except (OSError, subprocess.TimeoutExpired) as trouble:
            raise ClaudeSessionError(
                f"Claude Code の定額契約を確認できない: {trouble}"
            ) from trouble
        if done.returncode != 0:
            raise ClaudeSessionError(
                "Claude Code の定額契約へログインしていません。"
                + "claude auth login を実行してください"
            )
        status = self._json_text(done.stdout)
        if (
            status.get("loggedIn") is not True
            or status.get("authMethod") != "claude.ai"
            or status.get("apiProvider") != "firstParty"
        ):
            raise ClaudeSessionError("Claude Code が定額契約以外へ接続するため起動しません")

    def _clean_old(self, sessions: Path) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        for candidate in sessions.iterdir():
            if not candidate.is_dir():
                continue
            changed = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
            if changed >= cutoff:
                continue
            lock_path = candidate / "session.lock"
            try:
                with lock_path.open("a", encoding="utf-8") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    shutil.rmtree(candidate)
            except (OSError, BlockingIOError):
                continue

    def _workspace_root(self) -> Path:
        for candidate in (self._cwd, *self._cwd.parents):
            if (candidate / ".git").exists():
                return candidate
        return self._cwd

    def _trust_root(self, workspace_root: Path) -> Path:
        try:
            found = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={
                    key: value
                    for key, value in self._environment.items()
                    if key not in self._GIT_LOCAL_ENV
                },
            )
        except (OSError, subprocess.TimeoutExpired) as trouble:
            if (workspace_root / ".git").exists():
                raise ClaudeSessionError(
                    f"Git の作業場所の信頼を照合できない: {trouble}"
                ) from trouble
            return workspace_root
        if found.returncode != 0:
            if (workspace_root / ".git").exists():
                raise ClaudeSessionError(
                    "Git の作業場所の信頼を照合できない: "
                    + (found.stderr.strip() or "git rev-parse が失敗した")
                )
            return workspace_root
        common = Path(found.stdout.strip()).resolve()
        return common.parent if common.name == ".git" else workspace_root

    def _project_state(self, state: dict[str, object], root: Path) -> dict[str, object]:
        projects = self._mapping(state.get("projects"))
        if projects is None:
            return {}
        return self._mapping(projects.get(str(root))) or {}

    def _json_object(self, path: Path, *, missing_ok: bool) -> dict[str, object]:
        if missing_ok and not path.exists():
            return {}
        try:
            parsed: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
        except (OSError, json.JSONDecodeError) as trouble:
            raise ClaudeSessionError(f"{path} の設定を読めない: {trouble}") from trouble
        checked = self._mapping(parsed)
        if checked is None:
            raise ClaudeSessionError(f"{path} の設定が物の組ではない")
        return checked

    def _json_text(self, written: str) -> dict[str, object]:
        try:
            parsed: object = json.loads(written)  # pyright: ignore[reportAny]
        except json.JSONDecodeError as trouble:
            raise ClaudeSessionError("Claude Code の認証状態を読めない") from trouble
        checked = self._mapping(parsed)
        if checked is None:
            raise ClaudeSessionError("Claude Code の認証状態の形が違う")
        return checked

    def _write_json(self, path: Path, value: object) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened:
            json.dump(value, opened, ensure_ascii=False, separators=(",", ":"))

    def _strings(self, value: object) -> list[str]:
        return [item for item in self._sequence(value) if isinstance(item, str)]

    def _mapping(self, value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        checked: dict[str, object] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                return None
            checked[key] = item
        return checked

    def _sequence(self, value: object) -> list[object]:
        if not isinstance(value, list):
            return []
        return [item for item in value]  # pyright: ignore[reportUnknownVariableType]

    def _quoted(self, value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

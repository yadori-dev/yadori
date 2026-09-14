"""Linux で agy の設定と履歴を一回の宿りへ分ける。"""

from __future__ import annotations

import fcntl
import os
import shlex
import shutil
import subprocess
import sys
import termios
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, final

from yadori.adapter.recall.connection import NAME, MemoryConnection, MemoryConnectionError
from yadori.adapter.tool.agy_notice import AgyJson

SUPPORTED_AGY_VERSIONS = ("1.2.1:", "1.2.2:")


@dataclass(frozen=True)
class PreparedAgy:
    run_dir: Path
    argv: tuple[str, ...]
    environment: dict[str, str]
    lock: IO[str]


@final
class AgySession:
    """普段の設定を変更せず、端末とログインをそのまま使う。"""

    def __init__(
        self,
        home: Path,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        internal: bool = False,
    ) -> None:
        self._internal = internal
        self._home = home.resolve()
        self._cwd = (cwd or Path.cwd()).resolve()
        self._environment = dict(os.environ if environment is None else environment)
        self._usual = Path.home() / ".gemini"

    def launch(self) -> int:
        prepared = self.prepare()
        terminal = termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
        code = 1
        try:
            code = subprocess.call(prepared.argv, cwd=self._cwd, env=prepared.environment)
            return 1 if (prepared.run_dir / "failed").exists() else code
        finally:
            if terminal is not None:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, terminal)
            self.finish(prepared, code)

    def prepare(self) -> PreparedAgy:
        if sys.platform != "linux":
            raise ValueError("宿りの agy 対応は Linux 用です")
        for name in ("agy", "bwrap", "timeout"):
            if shutil.which(name, path=self._environment.get("PATH")) is None:
                raise ValueError(
                    f"{name} がありません。" + "agy と bubblewrap、coreutils を導入してください"
                )
        if self._home.is_relative_to(self._usual) or self._cwd.is_relative_to(self._usual):
            raise ValueError("agy の設定置き場を宿りの保存先・作業場所には使えません")
        if not self._internal:
            self._check_memory_connection()
        settings = self._settings()
        onboarding = self._onboarding()
        version = subprocess.run(
            ["agy", "changelog"],
            capture_output=True,
            text=True,
            check=True,
            env=self._environment,
            timeout=10,
        ).stdout.splitlines()
        if not version or version[0] not in SUPPORTED_AGY_VERSIONS:
            raise ValueError(
                "この宿りが対応する agy は 1.2.1 / 1.2.2 です。対応版を確認してください"
            )
        sessions = self._home / "agy/sessions"
        sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_dir = sessions / uuid.uuid4().hex
        run_dir.mkdir(mode=0o700)
        lock = (run_dir / "session.lock").open("w", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            self._write_settings(run_dir, settings, onboarding)
            environment = self._child_environment()
            argv = self._command(run_dir)
            return PreparedAgy(run_dir, tuple(argv), environment, lock)
        except BaseException:
            lock.close()
            shutil.rmtree(run_dir)
            raise

    def finish(self, prepared: PreparedAgy, returncode: int = 1) -> None:
        try:
            records = [
                AgyJson.object(path.read_text(encoding="utf-8"))
                for path in (prepared.run_dir / "turns").glob("*.json")
            ]
            unresolved = any(row.get("saved") is not True for row in records)
            if returncode != 0 or (prepared.run_dir / "failed").exists() or unresolved:
                _ = (prepared.run_dir / "interrupted").touch()
                print(
                    "警告: agy の回復用記録を残しました。次回の起動前に再試行します: "
                    + str(prepared.run_dir),
                    file=sys.stderr,
                )
                return
            shutil.rmtree(prepared.run_dir)
        except (OSError, ValueError) as trouble:
            print(f"警告: agy の記録を片付けず残しました: {trouble}", file=sys.stderr)
        finally:
            prepared.lock.close()

    def _check_memory_connection(self) -> None:
        path = self._usual / "config/mcp_config.json"
        text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        configured = AgyJson.object(text) if text else {}
        try:
            MemoryConnection.check_name(configured.get("mcpServers"))
        except MemoryConnectionError as trouble:
            raise ValueError(str(trouble)) from trouble

    def _settings(self) -> dict[str, object]:
        path = self._usual / "antigravity-cli/settings.json"
        settings = AgyJson.object(path.read_text(encoding="utf-8")) if path.exists() else {}
        if settings.get("modelProvider"):
            raise ValueError(
                "Google アカウントでログインした agy を使ってください。" + "認証方式は変更しません"
            )
        denied = (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_GEMINI_BASE_URL",
            "AGY_ADC_AUTH",
            "CLOUD_CODE_URL",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "CASCADE_GLOBAL_CONFIG_OVERRIDE",
        )
        if any(self._environment.get(key) for key in denied):
            raise ValueError("agy の認証・接続先を変更する環境変数があるため起動しません")
        trusted = settings.get("trustedWorkspaces", [])
        if not self._internal and (
            not isinstance(trusted, list)
            or not any(
                isinstance(path, str)
                and self._cwd.is_relative_to(Path(path).expanduser().resolve())
                for path in trusted  # pyright: ignore[reportUnknownVariableType]
            )
        ):
            raise ValueError("先に普段の agy でこの作業場所を開き、信頼を確認してください")
        keys = ("colorScheme", "altScreenMode", "verbosity", "runningLightSpeed", "enableTelemetry")
        borrowed = {key: settings[key] for key in keys if key in settings}
        for key in ("toolPermission", "enableTerminalSandbox"):
            if key in settings:
                borrowed[key] = settings[key]
        borrowed.update(
            {
                "trustedWorkspaces": [str(self._cwd)],
                "useG1Credits": False,
                "toolPermission": (
                    "strict" if settings.get("toolPermission") == "strict" else "request-review"
                ),
                "allowNonWorkspaceAccess": False,
                "notifications": False,
                "enableTelemetry": settings.get("enableTelemetry", False),
            }
        )
        if not self._internal:
            sources = [settings]
            roots = {
                self._usual / "config",
                *(parent / ".agents" for parent in (self._cwd, *self._cwd.parents)),
            }
            for root in roots:
                for filename in ("settings.json", "config.json"):
                    path = root / filename
                    if path.is_file():
                        text = path.read_text(encoding="utf-8").strip()
                        if text:
                            sources.append(AgyJson.object(text))
            try:
                borrowed["permissions"] = MemoryConnection.agy_permissions(sources)
            except MemoryConnectionError as trouble:
                raise ValueError(str(trouble)) from trouble
        return borrowed

    def _onboarding(self) -> dict[str, object]:
        path = self._usual / "antigravity-cli/cache/onboarding.json"
        if not path.exists():
            raise ValueError("先に普段の agy でログインと初回の案内を済ませてください")
        data = AgyJson.object(path.read_text(encoding="utf-8"))
        if (
            data.get("onboardingComplete") is not True
            or data.get("consumerOnboardingComplete") is not True
        ):
            raise ValueError("先に普段の agy で Google アカウントの初回案内を済ませてください")
        return {
            key: data.get(key, False)
            for key in (
                "onboardingComplete",
                "consumerOnboardingComplete",
                "enterpriseOnboardingComplete",
            )
        }

    def _write_settings(
        self,
        run_dir: Path,
        settings: dict[str, object],
        onboarding: dict[str, object],
    ) -> None:
        data = run_dir / "gemini/antigravity-cli"
        config = run_dir / "gemini/config"
        for path in (
            data / "cache",
            config / "agents/yadori",
            run_dir / "turns",
            run_dir / "empty",
        ):
            path.mkdir(parents=True, mode=0o700)
        _ = (run_dir / "empty-file").write_text("")
        AgyJson.write(data / "settings.json", settings)
        AgyJson.write(data / "cache/onboarding.json", onboarding)
        agent = (
            "---\nname: yadori\ndescription: 宿りとして作業に付き添う\n"
            "mainAgent: true\nsubagent: false\nexcludeDefaultComponents: false\n"
            "commandExecutionPolicy: off\ntools:\n"
            "  - view_file\n  - list_dir\n  - find_by_name\n  - grep_search\n"
            "  - write_to_file\n  - replace_file_content\n  - multi_replace_file_content\n"
            "  - run_command\n---\n"
            "あなたの名乗りと記憶は、発話前に渡される宿りの文脈に従います。"
            "下位担当、予約、背景作業、別の会話への切替は使いません。\n"
        )
        if self._internal:
            agent = (
                "---\nname: yadori\ndescription: 渡された文章だけから結果を返す\n"
                "mainAgent: true\nsubagent: false\nexcludeDefaultComponents: true\n"
                "tools: []\n---\n渡された指示に従い文章だけを返してください。"
            )
        _ = (config / "agents/yadori/agent.md").write_text(agent, encoding="utf-8")
        if self._internal:
            return
        AgyJson.write(
            config / "mcp_config.json",
            {
                "mcpServers": {
                    NAME: MemoryConnection.command(run_dir, self._home, self._environment)
                }
            },
        )
        hooks: dict[str, object] = {}
        for event, name in (("PreInvocation", "pre"), ("Stop", "stop"), ("PreToolUse", "tool")):
            command = shlex.join(
                [
                    "timeout",
                    "-k",
                    "2s",
                    "20s",
                    sys.executable,
                    "-I",
                    "-m",
                    "yadori",
                    "_agy-hook",
                    name,
                    str(run_dir),
                ]
            )
            # agy はフックの失敗を無視するため、通知元の起動自体へ失敗を伝える。
            command = f"printf %s {name} > {shlex.quote(str(run_dir / 'stage'))} && " + command
            command += (
                f" || {{ touch {shlex.quote(str(run_dir / 'failed'))} "
                + f'{shlex.quote(str(run_dir / ("failed-" + name)))}; kill -TERM "$PPID"; }}'
            )
            handler = {"type": "command", "command": command, "timeout": 30}
            hooks[event] = [{"matcher": "*", "hooks": [handler]}] if name == "tool" else [handler]
        AgyJson.write(config / "hooks.json", {"yadori": hooks})

    def _child_environment(self) -> dict[str, str]:
        blocked_prefixes = ("AGY_", "ANTIGRAVITY_", "CASCADE_", "GIT_", "GOOGLE_", "GEMINI_")
        env = {
            key: value
            for key, value in self._environment.items()
            if not key.startswith(blocked_prefixes)
        }
        env.update(
            {
                "YADORI_HOME": str(self._home),
                "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
                "AGY_CLI_HIDE_ACCOUNT_INFO": "true",
            }
        )
        return env

    def _command(self, run_dir: Path) -> list[str]:
        argv = [
            "bwrap",
            "--die-with-parent",
            "--bind",
            "/",
            "/",
            "--unshare-pid",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--bind",
            str(run_dir / "gemini"),
            str(self._usual),
        ]
        for path in self._automatic_paths():
            replacement = str(run_dir / "empty") if path.is_dir() else str(run_dir / "empty-file")
            argv.extend(["--ro-bind", replacement, str(path)])
        argv.extend(
            [
                "--chdir",
                str(self._cwd),
                "--",
                "agy",
                "--agent",
                "yadori",
                "--add-dir",
                str(self._cwd),
            ]
        )
        return argv

    def _automatic_paths(self) -> list[Path]:
        hidden: set[Path] = set()
        for parent in (self._cwd, *self._cwd.parents, Path.home()):
            for name in (".agents", "GEMINI.md", "AGENTS.md"):
                path = parent / name
                if path.exists():
                    hidden.add(path)
        for root, dirs, files in os.walk(self._cwd):
            for name in ("GEMINI.md", "AGENTS.md"):
                if name in files:
                    hidden.add(Path(root) / name)
            if ".agents" in dirs:
                hidden.add(Path(root) / ".agents")
            dirs[:] = [
                name
                for name in dirs
                if name not in {".git", ".venv", "node_modules", ".agents", ".gemini"}
            ]
        return sorted(
            path for path in hidden if not any(parent in hidden for parent in path.parents)
        )

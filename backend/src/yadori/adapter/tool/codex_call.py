"""Codex の一回実行から文章だけを受け取る。通常の設定・指示・履歴を借りない。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import final

from yadori.adapter.tool.claude_code import ToolCallFailed


@final
class CodexCall:
    def __init__(self, model: str | None, wait_seconds: int) -> None:
        self._model = model
        self._wait_seconds = wait_seconds

    @property
    def model(self) -> str:
        return f"codex:{self._model or 'default'}"

    def ask(self, preface: str, spoken: str) -> str:
        try:
            with tempfile.TemporaryDirectory(prefix="yadori-codex-call-") as temporary:
                return self._ask_in(Path(temporary), preface, spoken)
        except (OSError, subprocess.SubprocessError) as trouble:
            raise ToolCallFailed(
                f"Codex の一回実行が失敗しました: {type(trouble).__name__}"
            ) from trouble

    def _ask_in(self, root: Path, preface: str, spoken: str) -> str:
        if any(
            os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")
        ):
            raise ToolCallFailed(
                "Codex の別の認証・接続先が指定されています。ChatGPT ログインを使ってください"
            )
        config = root / "config"
        work = root / "work"
        config.mkdir()
        work.mkdir()
        usual = Path(os.environ.get("CODEX_CONFIG_DIR", str(Path.home() / ".codex"))).expanduser()
        credential = usual / "auth.json"
        if credential.is_file():
            (config / "auth.json").symlink_to(credential.resolve())
        instructions = root / "instructions.md"
        _ = instructions.write_text(preface, encoding="utf-8")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("CODEX_", "GIT_"))
        }
        env.update({"CODEX_HOME": str(config), "CODEX_SQLITE_HOME": str(config)})
        checked = subprocess.run(
            ["codex", "login", "status"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if checked.returncode or "ChatGPT" not in checked.stdout + checked.stderr:
            raise ToolCallFailed(
                "Codex の ChatGPT ログインを確認できません。codex login を実行してください"
            )
        output = root / "reply.txt"
        argv = [
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(output),
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'web_search="disabled"',
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.apply_patch_freeform=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.skills=false",
            "-c",
            'developer_instructions=""',
            "-c",
            f"model_instructions_file={json.dumps(str(instructions))}",
        ]
        if self._model:
            argv.extend(["--model", self._model])
        done = subprocess.run(
            [*argv, "-"],
            input=spoken,
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=self._wait_seconds,
            check=False,
        )
        if done.returncode:
            raise ToolCallFailed(
                f"Codex の一回実行が失敗しました（終了状態 {done.returncode}）。再送しません"
            )
        text = output.read_text(encoding="utf-8").strip() if output.is_file() else ""
        if not text:
            raise ToolCallFailed("Codex の返事が空でした")
        return text

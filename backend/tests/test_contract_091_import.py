"""宿りのフックがない通常CLIで会話し、実ログの完成判定を照合する。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from yadori.adapter.importing.records import SessionLogs
from yadori.adapter.tool.agy_session import AgySession
from yadori.domain.importing.model import Provider

PROMPT = (
    "架空の検証用会話です。試験記録の保管方針は、分類ラベル「星砂」、月ごとに分割、"
    + "21日で削除し外部公開しない、です。道具を使わず、この決定を短く復唱してください。"
)


@pytest.mark.contract
@pytest.mark.parametrize("provider", ["claude", "codex", "agy"])
def test_IT_091_001_通常CLIの実ログから完成した原文を取り込める(
    tmp_path: Path, provider: Provider
) -> None:
    if shutil.which(provider) is None:
        pytest.skip("ログインした通常CLIが必要")
    usual = tmp_path / "usual"
    usual.mkdir()
    env = dict(os.environ)
    for key in (
        "YADORI_HOME",
        "YADORI_SETTINGS_SNAPSHOT",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        _ = env.pop(key, None)
    with tempfile.TemporaryDirectory(dir=Path.cwd() if provider == "agy" else None) as work:
        cwd = Path(work)
        if provider == "codex":
            (usual / "auth.json").symlink_to(Path.home() / ".codex/auth.json")
            _ = (usual / "config.toml").write_text("")
            env["CODEX_HOME"] = str(usual)
            env["CODEX_SQLITE_HOME"] = str(usual)
            command = ["codex", "exec", "--skip-git-repo-check", "--json", PROMPT]
            result = subprocess.run(
                command, cwd=cwd, env=env, text=True, capture_output=True, timeout=120
            )
            files = tuple(usual.rglob("rollout*.jsonl"))
        elif provider == "claude":
            (usual / ".credentials.json").symlink_to(Path.home() / ".claude/.credentials.json")
            env["CLAUDE_CONFIG_DIR"] = str(usual)
            result = subprocess.run(
                ["claude", "--print", PROMPT],
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=120,
            )
            files = tuple((usual / "projects").rglob("*.jsonl"))
        else:
            # 通常設定への書き込みを避ける配置だけを借り、宿りの接続・フック・担当は使わない。
            home = tmp_path / "isolation"
            home.mkdir()
            _ = (home / "dweller.toml").write_text(
                'id="fixture"\nname="fixture"\nnickname="fixture"\nowner="fixture"\n'
            )
            _ = (home / "identity.md").write_text("fixture")
            prepared = AgySession(home, cwd=cwd, environment=env).prepare()
            try:
                config = prepared.run_dir / "gemini/config"
                _ = (config / "hooks.json").write_text("{}")
                _ = (config / "mcp_config.json").write_text("{}")
                command = list(prepared.argv)
                start = command.index("--agent")
                del command[start : start + 2]
                command += ["--print", PROMPT, "--output-format", "json"]
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    env=prepared.environment,
                    text=True,
                    capture_output=True,
                    timeout=120,
                )
                files = tuple(prepared.run_dir.rglob("transcript_full.jsonl"))
            finally:
                prepared.lock.close()
        assert result.returncode == 0, result.stderr[-1000:]
        assert files
        records = tuple(
            record for path in files for record in SessionLogs().read(provider, path).conversations
        )
        assert len(records) == 1
        assert records[0].utterance == PROMPT
        assert "星砂" in records[0].reply and "21日" in records[0].reply

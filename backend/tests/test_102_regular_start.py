"""普段の設定を使い、宿りの追加設定だけを起動へ渡す。"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

from yadori.adapter.importing.records import RecordJson
from yadori.adapter.tool.agy_notice import AgyJson
from yadori.adapter.tool.claude_session import ClaudeSession, PreparedClaude
from yadori.adapter.tool.codex_session import CodexSession


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_ST_102_001_IT_102_001_通常環境を保ち信頼や権限を合成しない(
    tmp_path: Path, provider: str
) -> None:
    work = tmp_path / "work"
    usual = tmp_path / "usual"
    work.mkdir()
    usual.mkdir()
    _ = (work / ".mcp.json").write_text('{"mcpServers":{"undecided":{"command":"true"}}}')
    _ = (work / ".claude").mkdir()
    _ = (work / ".claude/settings.json").write_text('{"hooks":{}}')
    _ = (work / ".codex").mkdir()
    _ = (work / ".codex/config.toml").write_text('model_reasoning_effort="high"\n')
    marker = usual / "history.jsonl"
    _ = marker.write_text("existing conversation\n")
    environment = {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(usual),
        "CODEX_HOME": str(usual),
        "CODEX_SQLITE_HOME": str(usual),
    }
    session_type = ClaudeSession if provider == "claude" else CodexSession
    session = session_type(tmp_path / "dweller", work, environment, sys.executable)
    prepared = session.prepare()
    try:
        for name, value in environment.items():
            if name not in {"YADORI_HOME", "YADORI_RUN_DIR"}:
                assert prepared.environment[name] == value
        assert "--dangerously-bypass-hook-trust" not in prepared.argv
        assert "--strict-mcp-config" not in prepared.argv
        assert "--setting-sources" not in prepared.argv
        assert not (prepared.run_dir / "auth.json").exists()
        assert not (prepared.run_dir / ".credentials.json").exists()
        if provider == "claude":
            settings = AgyJson.object((prepared.run_dir / "yadori-settings.json").read_text())
            assert set(settings) == {"hooks"}
        else:
            overrides: dict[str, object] = tomllib.loads("\n".join(prepared.argv[2::2]))
            assert set(overrides) == {"hooks", "mcp_servers"}
            assert set(RecordJson.mapping(overrides["mcp_servers"])) == {"yadori_memory"}
    finally:
        if isinstance(prepared, PreparedClaude):
            assert isinstance(session, ClaudeSession)
            session.finish(prepared)
        else:
            assert isinstance(session, CodexSession)
            session.finish(prepared)
    assert marker.read_text() == "existing conversation\n"
    assert not prepared.run_dir.exists()


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_ST_102_002_IT_102_001_再開の引数と通常環境を子プロセスへ渡す(
    tmp_path: Path, provider: str
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    executable = tmp_path / provider
    _ = executable.write_text(
        f"#!{sys.executable}\n"
        + "import os,sys,json,pathlib\n"
        + "pathlib.Path('observed.json').write_text(json.dumps({'args':sys.argv[1:],"
        + "'home':os.environ['REGULAR_CONFIG']}))\n"
    )
    executable.chmod(0o755)
    session_type = ClaudeSession if provider == "claude" else CodexSession
    session = session_type(
        tmp_path / "dweller", work, {**os.environ, "REGULAR_CONFIG": "usual"}, str(executable)
    )
    arguments = ("--resume", "conversation") if provider == "claude" else ("resume", "--last")
    assert session.launch(arguments) == 0
    observed = AgyJson.object((work / "observed.json").read_text())
    args = observed["args"]
    assert isinstance(args, list)
    assert args[-2:] == list(arguments)
    assert observed["home"] == "usual"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_IT_102_001_通常設定の同名MCPを上書きしない(tmp_path: Path, provider: str) -> None:
    from yadori.adapter.tool.claude_session import ClaudeSessionError
    from yadori.adapter.tool.codex_session import CodexSessionError

    work, usual = tmp_path / "work", tmp_path / "usual"
    work.mkdir()
    usual.mkdir()
    if provider == "claude":
        path = usual / ".claude.json"
        original = '{"mcpServers":{"yadori_memory":{"command":"existing"}}}'
    else:
        path = usual / "config.toml"
        original = '[mcp_servers.yadori_memory]\ncommand="existing"\n'
    _ = path.write_text(original)
    environment = {"CLAUDE_CONFIG_DIR": str(usual), "CODEX_HOME": str(usual)}
    session_type = ClaudeSession if provider == "claude" else CodexSession
    with pytest.raises((ClaudeSessionError, CodexSessionError), match="予約名"):
        _ = session_type(tmp_path / "dweller", work, environment, sys.executable).prepare()
    assert path.read_text() == original

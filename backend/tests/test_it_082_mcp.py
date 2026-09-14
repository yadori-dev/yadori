"""MCPの実際の入出力と、一時設定の接続・拒否を確認する。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_st_082_recall import Fixture, parsed, reference
from yadori.adapter.recall.connection import MemoryConnection, MemoryConnectionError


def test_ST_082_004_IT_082_003_stdioで道具を認識し起動時の人物の原文を取得する(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    ref = reference(parsed(fixture.tools.search(fixture.turn, "資料の整理方針")))
    _ = (tmp_path / "dweller.toml").write_text(
        'id="sora"\nname="そら"\nnickname="そら"\nowner="架空"\n'
    )
    _ = (tmp_path / "identity.md").write_text("そらです")

    snapshot_dir = tmp_path / "settings-run-test"
    snapshot_dir.mkdir()
    snapshot = snapshot_dir / "settings.toml"
    person = '[people.sora]\nname="そら"\nnickname="そら"\nowner="架空"\nidentity="そらです"\n'
    _ = snapshot.write_text('version=1\nactive="sora"\n' + person)
    _ = (tmp_path / "settings.toml").write_text(
        'version=1\nactive="another"\n[people.another]\nname="別人"\nnickname="別人"\nowner="架空"\nidentity="別人です"\n'
    )

    async def exercise() -> None:
        env = {key: value for key, value in os.environ.items() if key != "YADORI_SETTINGS_SNAPSHOT"}
        env["YADORI_HOME"] = str(tmp_path)
        env["YADORI_SETTINGS_SNAPSHOT"] = str(snapshot)
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "yadori", "_memory-mcp", str(tmp_path)], env=env
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            _ = await session.initialize()
            listed = await session.list_tools()
            assert {one.name for one in listed.tools} == {"recall_search", "recall_get"}
            assert all(one.annotations and one.annotations.read_only_hint for one in listed.tools)
            response = await session.call_tool(
                "recall_get", {"turn": fixture.turn, "reference": ref}
            )
            assert not response.is_error
            first = response.content[0]
            assert first.type == "text"
            result = parsed(first.text)
            assert result["complete"] is True and "見出しごと" in str(result["text"])
            wrong = await session.call_tool(
                "recall_get", {"turn": fixture.turn, "reference": "other"}
            )
            content = wrong.content[0]
            assert content.type == "text" and parsed(content.text)["status"] == "invalid_reference"

    try:
        asyncio.run(exercise())
    finally:
        fixture.store.close()


@pytest.mark.parametrize("rule", ["mcp__yadori_memory", "mcp__yadori_memory__*", "mcp__*", "*"])
@pytest.mark.parametrize("category", ["deny", "ask"])
def test_ST_082_007_IT_082_004_既存の拒否や確認を上書きしない(rule: str, category: str) -> None:
    settings: dict[str, object] = {"permissions": {category: [rule]}}
    before = json.dumps(settings)
    with pytest.raises(MemoryConnectionError):
        MemoryConnection.claude_permissions(settings)
    assert json.dumps(settings) == before


def test_ST_082_007_IT_082_004_記憶の読み取りだけ許可し同名の接続を上書きしない(
    tmp_path: Path,
) -> None:
    with pytest.raises(MemoryConnectionError, match="予約名"):
        MemoryConnection.check_name({"yadori_memory": {"command": "existing"}})
    settings: dict[str, object] = {"permissions": {"allow": ["Read"], "deny": ["Write"]}}
    MemoryConnection.claude_permissions(settings)
    assert settings == {
        "permissions": {
            "allow": [
                "Read",
                "mcp__yadori_memory__recall_search",
                "mcp__yadori_memory__recall_get",
            ],
            "deny": ["Write"],
        }
    }
    config = MemoryConnection.codex(tmp_path, tmp_path, {"YADORI_SETTINGS_SNAPSHOT": "/snapshot"})
    assert config["required"] is True and config["enabled_tools"] == ["recall_search", "recall_get"]
    assert config["env"] == {"YADORI_HOME": str(tmp_path), "YADORI_SETTINGS_SNAPSHOT": "/snapshot"}


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_IT_082_004_作業場所やPYTHONPATHの同名コードを起動しない(
    tmp_path: Path, provider: str
) -> None:
    import subprocess

    work = tmp_path / "work"
    fake = work / "yadori"
    fake.mkdir(parents=True)
    _ = (fake / "__init__.py").write_text("")
    _ = (fake / "__main__.py").write_text("print('PROJECT_PACKAGE_EXECUTED')")
    config = (
        MemoryConnection.codex(tmp_path, tmp_path, {})
        if provider == "codex"
        else MemoryConnection.command(tmp_path, tmp_path, {})
    )
    args = config["args"]
    assert isinstance(args, list) and all(isinstance(one, str) for one in args)  # pyright: ignore[reportUnknownVariableType]
    argv = [str(config["command"]), *(str(one) for one in args)]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    env = dict(os.environ, YADORI_HOME=str(tmp_path), PYTHONPATH=str(work))
    _ = env.pop("YADORI_SETTINGS_SNAPSHOT", None)
    result = subprocess.run(
        argv, cwd=work, env=env, input="", capture_output=True, text=True, timeout=20
    )
    assert "PROJECT_PACKAGE_EXECUTED" not in result.stdout
    assert "記憶の接続を起動できません" in result.stderr


@pytest.mark.parametrize("key", ["disabledMcpServers", "disabledMcpjsonServers"])
def test_ST_082_007_Claudeの無効化指定があれば理由を示して起動を断る(
    tmp_path: Path, key: str
) -> None:
    from yadori.adapter.tool.claude_session import ClaudeSession, ClaudeSessionError

    home = tmp_path / "home"
    work = tmp_path / "work"
    usual = tmp_path / "usual"
    home.mkdir()
    work.mkdir()
    usual.mkdir()
    _ = (usual / ".claude.json").write_text(
        json.dumps({"projects": {str(work): {key: ["yadori_memory"]}}})
    )
    session = ClaudeSession(
        home, cwd=work, environment={"CLAUDE_CONFIG_DIR": str(usual)}, executable=sys.executable
    )
    with pytest.raises(ClaudeSessionError, match="無効化"):
        _ = session.prepare()


def test_IT_082_004_Codexの接続設定を階層を保って書く(tmp_path: Path) -> None:
    import tomllib

    from yadori.adapter.tool.codex_session import CodexSession

    home = tmp_path / "home"
    work = tmp_path / "work"
    usual = tmp_path / "usual"
    home.mkdir()
    work.mkdir()
    usual.mkdir()
    session = CodexSession(
        home, cwd=work, environment={"CODEX_CONFIG_DIR": str(usual)}, executable=sys.executable
    )
    prepared = session.prepare()
    try:
        data: dict[str, object] = tomllib.loads((prepared.run_dir / "config.toml").read_text())
        servers = data["mcp_servers"]
        assert isinstance(servers, dict)
        server: object = servers["yadori_memory"]  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(server, dict)
        assert server["env"] == {"YADORI_HOME": str(home)}
        assert server["tools"] == {
            "recall_search": {"approval_mode": "approve", "output_token_limit": 5000},
            "recall_get": {"approval_mode": "approve", "output_token_limit": 5000},
        }
    finally:
        session.finish(prepared)


def test_IT_082_004_完了済みの初期案内を保ち口座情報や他履歴を写さない(tmp_path: Path) -> None:
    from yadori.adapter.tool.claude_session import ClaudeSession

    home, work, usual = tmp_path / "home", tmp_path / "work", tmp_path / "usual"
    for path in (home, work, usual):
        path.mkdir()
    state = {
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "2.1.72",
        "theme": "light",
        "oauthAccount": {"email": "not-copied@example.invalid"},
        "history": "not-copied",
    }
    _ = (usual / ".claude.json").write_text(json.dumps(state))
    session = ClaudeSession(
        home, work, {"CLAUDE_CONFIG_DIR": str(usual)}, executable=sys.executable
    )
    prepared = session.prepare()
    try:
        copied = parsed((prepared.run_dir / ".claude.json").read_text())
        assert copied["hasCompletedOnboarding"] is True
        assert copied["lastOnboardingVersion"] == "2.1.72" and copied["theme"] == "light"
        assert "oauthAccount" not in copied and "history" not in copied
    finally:
        session.finish(prepared)

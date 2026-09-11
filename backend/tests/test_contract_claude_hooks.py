"""Claude Code の正式なフック入力を、実物で固定した形と突き合わせる。"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ClaudeSession


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _object(path: Path) -> dict[str, object]:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
    assert isinstance(parsed, dict)
    checked: dict[str, object] = {}
    for key, value in parsed.items():  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(key, str)
        checked[key] = value
    return checked


@pytest.mark.contract
def test_IT_068_004_実際のClaudeが発話と返事を同じ識別子でフックへ渡す(
    tmp_path: Path,
) -> None:
    executable = shutil.which("claude")
    credential = Path.home() / ".claude" / ".credentials.json"
    if executable is None or not credential.exists():
        pytest.skip("定額契約へログインした Claude Code が手元に無い")

    config = tmp_path / "claude"
    captured = tmp_path / "captured"
    captured.mkdir()
    recorder = tmp_path / "record.py"
    _ = recorder.write_text(
        """import json, pathlib, sys
event, out = sys.argv[1:]
pathlib.Path(out, event + '.json').write_text(sys.stdin.read(), encoding='utf-8')
if event == 'UserPromptSubmit':
    print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
        '返事には必ず YADORI_CONTEXT を含めてください。'}}))
""",
        encoding="utf-8",
    )
    credential_link = config / ".credentials.json"
    config.mkdir()
    credential_link.symlink_to(credential)
    hooks: dict[str, object] = {}
    for event in ("UserPromptSubmit", "Stop"):
        command = " ".join(
            shlex.quote(part) for part in (sys.executable, str(recorder), event, str(captured))
        )
        hooks[event] = [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}]
    settings = config / "settings.json"
    _write_json(settings, {"hooks": hooks, "disableAllHooks": False})
    mcp = config / "mcp.json"
    _write_json(mcp, {"mcpServers": {}})
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
        }
    }
    environment["CLAUDE_CONFIG_DIR"] = str(config)
    environment["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    done = subprocess.run(
        [
            executable,
            "--setting-sources",
            "user",
            "--settings",
            str(settings),
            "--mcp-config",
            str(mcp),
            "--strict-mcp-config",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--model",
            "haiku",
            "--print",
            "短く返事をしてください。",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr

    submitted = _object(captured / "UserPromptSubmit.json")
    stopped = _object(captured / "Stop.json")
    assert submitted["hook_event_name"] == "UserPromptSubmit"
    assert submitted["prompt"] == "短く返事をしてください。"
    assert stopped["hook_event_name"] == "Stop"
    assert isinstance(stopped["last_assistant_message"], str)
    assert "YADORI_CONTEXT" in stopped["last_assistant_message"]
    session_id = submitted["session_id"]
    prompt_id = submitted["prompt_id"]
    assert isinstance(session_id, str) and isinstance(prompt_id, str)
    assert stopped["session_id"] == session_id and stopped["prompt_id"] == prompt_id
    _ = uuid.UUID(session_id)
    _ = uuid.UUID(prompt_id)


@pytest.mark.contract
def test_IT_068_005_実際のClaudeへ宿りの文脈を渡して停止時に一往復を確定する(
    tmp_path: Path,
) -> None:
    executable = shutil.which("claude")
    credential = Path.home() / ".claude" / ".credentials.json"
    model_cache = Path.home() / ".yadori" / "models"
    if executable is None or not credential.exists() or not model_cache.is_dir():
        pytest.skip("定額契約、Claude Code、取得済みの埋め込みが手元に無い")

    home = tmp_path / "yadori"
    home.mkdir()
    _ = (home / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (home / "identity.md").write_text(
        "わたしはそらです。返事には必ず YADORI_E2E を含めます。\n", encoding="utf-8"
    )
    (home / "models").symlink_to(model_cache, target_is_directory=True)
    usual = tmp_path / "usual"
    usual.mkdir()
    (usual / ".credentials.json").symlink_to(credential)
    _write_json(usual / ".claude.json", {})
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
        }
    }
    environment.update(
        {
            "CLAUDE_CONFIG_DIR": str(usual),
            "YADORI_HOME": str(home),
            "HF_HUB_OFFLINE": "1",
        }
    )
    session = ClaudeSession(home, tmp_path, environment, executable)
    prepared = session.prepare()
    try:
        done = subprocess.run(
            [
                *prepared.argv,
                "--no-session-persistence",
                "--disable-slash-commands",
                "--model",
                "haiku",
                "--print",
                "短く返事をしてください。",
            ],
            cwd=tmp_path,
            env=prepared.environment,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        memories = SqliteMemories(home / "memories.sqlite")
        try:
            assert memories.count_episodes("sora") == 1
            episode = memories.recent("sora", 1)[0]
            assert episode.utterance == "短く返事をしてください。"
            assert "YADORI_E2E" in episode.reply
            assert episode.recalled_at is not None
            assert episode.source is not None and episode.source.startswith("claude:")
            assert len(memories.shifts("sora")) == 1
        finally:
            memories.close()
    finally:
        session.finish(prepared)

    assert not prepared.run_dir.exists()

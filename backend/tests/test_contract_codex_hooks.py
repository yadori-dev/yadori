"""Codex の正式なフック入力を、実物で固定した形と突き合わせる。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import CodexSession


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
def test_IT_070_005_実際のCodexが発話と返事を同じ識別子でフックへ渡す(
    tmp_path: Path,
) -> None:
    executable = shutil.which("codex")
    credential = Path.home() / ".codex" / "auth.json"
    if executable is None or not credential.exists():
        pytest.skip("ChatGPT 契約へログインした Codex が手元に無い")

    work = tmp_path / "work"
    work.mkdir()
    _ = subprocess.run(["git", "init"], cwd=work, capture_output=True, check=True)
    _ = subprocess.run(["git", "config", "user.name", "test"], cwd=work, check=True)
    _ = subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    _ = (work / "README.md").write_text("test\n", encoding="utf-8")
    _ = subprocess.run(["git", "add", "."], cwd=work, check=True)
    _ = subprocess.run(["git", "commit", "-m", "init"], cwd=work, check=True)

    config = tmp_path / "codex"
    captured = tmp_path / "captured"
    captured.mkdir()
    recorder = tmp_path / "record.sh"
    recorder_script = (
        "#!/bin/sh\n"
        'event="$1"\n'
        f'out="{captured}"\n'
        'cat > "$out/$event.json"\n'
        'if [ "$event" = "UserPromptSubmit" ]; then\n'
        '    printf \'{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit",\'\n'
        '    printf \'"additionalContext":"返事には必ず YADORI_CONTEXT を含めてください。\'\n'
        "    printf '必ず末尾に「【気持ち】+0.2 穏やか」と書いてください。\"}}'\n"
        "fi\n"
        "exit 0\n"
    )
    _ = recorder.write_text(recorder_script, encoding="utf-8")
    recorder.chmod(0o755)

    config.mkdir()
    (config / "auth.json").symlink_to(credential)
    _ = (config / "config.toml").write_text(
        f'[projects."{work.resolve()}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    hooks_cfg = {
        "description": "test contract hooks",
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": f"{recorder} UserPromptSubmit"}]}
            ],
            "Stop": [{"hooks": [{"type": "command", "command": f"{recorder} Stop"}]}],
        },
    }
    _write_json(config / "hooks.json", hooks_cfg)

    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "CODEX_API_KEY",
        }
    }
    environment["CODEX_HOME"] = str(config)

    done = subprocess.run(
        [
            executable,
            "exec",
            "--dangerously-bypass-hook-trust",
            "--cd",
            str(work),
            "短く返事をしてください。",
        ],
        cwd=work,
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
    turn_id = submitted["turn_id"]
    assert isinstance(session_id, str) and isinstance(turn_id, str)
    assert stopped["session_id"] == session_id and stopped["turn_id"] == turn_id
    _ = uuid.UUID(session_id)
    _ = uuid.UUID(turn_id)


@pytest.mark.contract
def test_IT_070_005_実際のCodexへ宿りの文脈を渡して停止時に一往復を確定する(
    tmp_path: Path,
) -> None:
    executable = shutil.which("codex")
    credential = Path.home() / ".codex" / "auth.json"
    model_cache = Path.home() / ".yadori" / "models"
    if executable is None or not credential.exists() or not model_cache.is_dir():
        pytest.skip("ChatGPT 契約、Codex、取得済みの埋め込みが手元に無い")

    work = tmp_path / "work"
    work.mkdir()
    _ = subprocess.run(["git", "init"], cwd=work, capture_output=True, check=True)
    _ = subprocess.run(["git", "config", "user.name", "test"], cwd=work, check=True)
    _ = subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    _ = (work / "README.md").write_text("test\n", encoding="utf-8")
    _ = subprocess.run(["git", "add", "."], cwd=work, check=True)
    _ = subprocess.run(["git", "commit", "-m", "init"], cwd=work, check=True)

    home = tmp_path / "yadori"
    home.mkdir()
    _ = (home / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    identity_prompt = (
        "わたしはそらです。返事には必ず YADORI_E2E を含め、"
        "末尾に「【気持ち】+0.2 穏やか」と一行だけ書いてください。\n"
    )
    _ = (home / "identity.md").write_text(identity_prompt, encoding="utf-8")
    (home / "models").symlink_to(model_cache, target_is_directory=True)

    usual = tmp_path / "usual"
    usual.mkdir()
    (usual / "auth.json").symlink_to(credential)
    _ = (usual / "config.toml").write_text(
        f'[projects."{work.resolve()}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "CODEX_API_KEY",
        }
    }
    environment.update(
        {
            "CODEX_CONFIG_DIR": str(usual),
            "YADORI_HOME": str(home),
            "HF_HUB_OFFLINE": "1",
        }
    )

    session = CodexSession(home, work, environment, executable)
    prepared = session.prepare()
    try:
        done = subprocess.run(
            [
                executable,
                "exec",
                "--dangerously-bypass-hook-trust",
                "--cd",
                str(work),
                "短く返事をしてください。",
            ],
            cwd=work,
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
            assert episode.source is not None and episode.source.startswith("codex:")
            assert len(memories.shifts("sora")) == 1
        finally:
            memories.close()
    finally:
        session.finish(prepared)

    assert not prepared.run_dir.exists()

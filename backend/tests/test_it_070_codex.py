"""IT-070: Codex を宿りとして起こし、ライフサイクルフックで一往復を続ける。"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.sora import fixed
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import (
    CodexSession,
    CodexSessionError,
    CodexWords,
    PendingStore,
    PendingTurn,
    StaleSessionCleaner,
)
from yadori.domain.memory import (
    Character,
    Episode,
    Found,
    Identity,
    Mood,
    Recollection,
    Retrieval,
    State,
)
from yadori.infrastructure.codex import CodexHook
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup


def _home(path: Path) -> Path:
    path.mkdir()
    _ = (path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (path / "identity.md").write_text("わたしはそらです。園芸を好みます。\n", encoding="utf-8")
    return path


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    checked: dict[str, object] = {}
    for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(key, str)
        checked[key] = item
    return checked


def _without_parent_git() -> dict[str, str]:
    local = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
    }
    return {k: v for k, v in os.environ.items() if k not in local}


def _init_git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _ = subprocess.run(["git", "init", "-q", str(path)], check=True, env=_without_parent_git())


def _hook(
    event: str,
    payload: Mapping[str, object],
    run_dir: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    startup = Startup(home, default=fixed(CharacterPairs()))
    return CodexHook(event, run_dir, home, startup).run()


class TestIT070001:
    """AD-070-001: Codex の起動と借りる設定を adapter/tool へ閉じる。"""

    def test_IT_070_001_普段の設定と作業場所設定を合成し危険設定を除外する(
        self, tmp_path: Path
    ) -> None:
        home = _home(tmp_path / "home")
        usual = tmp_path / "usual_codex"
        usual.mkdir()
        _ = (usual / "config.toml").write_text(
            'model = "gpt-5"\nmodel_reasoning_effort = "high"\n'
            + 'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
            + f'[projects."{tmp_path / "work"}"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        work = tmp_path / "work"
        work.mkdir()

        session = CodexSession(
            home=home,
            cwd=work,
            environment={"CODEX_CONFIG_DIR": str(usual)},
            executable="true",
        )
        prepared = session.prepare()
        try:
            config_text = (prepared.run_dir / "config.toml").read_text(encoding="utf-8")
            # 許可された設定が写っている
            assert 'model = "gpt-5"' in config_text
            assert 'model_reasoning_effort = "high"' in config_text
            # 危険な設定（never, danger-full-access）は除外されている
            assert "approval_policy" not in config_text
            assert "danger-full-access" not in config_text
            # 作業場所の信頼が設定されている
            assert f'[projects."{work.resolve()}"]' in config_text
            # hooks.json が作られている
            assert (prepared.run_dir / "hooks.json").exists()
        finally:
            session.finish(prepared)

    def test_IT_070_001_未信頼の作業場所では起動を断る(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        usual = tmp_path / "usual_codex"
        usual.mkdir()
        _ = (usual / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
        work = tmp_path / "work"
        work.mkdir()
        # 作業場所に設定やフックを置く
        (work / ".codex").mkdir()
        _ = (work / ".codex" / "config.toml").write_text('theme = "dark"\n', encoding="utf-8")

        session = CodexSession(
            home=home,
            cwd=work,
            environment={"CODEX_CONFIG_DIR": str(usual)},
            executable="true",
        )
        with pytest.raises(CodexSessionError) as trouble:
            _ = session.prepare()
        assert "信頼されていません" in str(trouble.value)

    def test_IT_070_001_worktreeでは元のチェックアウト直下で信頼を照合する(
        self, tmp_path: Path
    ) -> None:
        home = _home(tmp_path / "home")
        repo = tmp_path / "repo"
        _init_git(repo)
        _ = (repo / "README.md").write_text("sample\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "."], cwd=repo, check=True, env=_without_parent_git())
        _ = subprocess.run(
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "init",
            ],
            cwd=repo,
            check=True,
            env=_without_parent_git(),
        )
        worktree = tmp_path / "worktree"
        _ = subprocess.run(
            ["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
            cwd=repo,
            check=True,
            env=_without_parent_git(),
        )

        usual = tmp_path / "usual_codex"
        usual.mkdir()
        # 元の repo 側を trusted に登録する
        _ = (usual / "config.toml").write_text(
            f'[projects."{repo.resolve()}"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        # worktree 側に .codex 設定を置く
        (worktree / ".codex").mkdir()
        _ = (worktree / ".codex" / "config.toml").write_text('theme = "light"\n', encoding="utf-8")

        session = CodexSession(
            home=home,
            cwd=worktree,
            environment={"CODEX_CONFIG_DIR": str(usual)},
            executable="true",
        )
        prepared = session.prepare()
        try:
            config_text = (prepared.run_dir / "config.toml").read_text(encoding="utf-8")
            # 元の repo が信頼されているため起動できる
            assert f'[projects."{repo.resolve()}"]' in config_text
            assert 'theme = "light"' in config_text
        finally:
            session.finish(prepared)

    def test_IT_070_001_ChatGPT以外の認証やAPIキーでは起動を断る(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        usual = tmp_path / "usual_codex"
        usual.mkdir()
        work = tmp_path / "work"
        work.mkdir()

        # OPENAI_API_KEY 環境変数がある場合は起動中止
        session = CodexSession(
            home=home,
            cwd=work,
            environment={"CODEX_CONFIG_DIR": str(usual), "OPENAI_API_KEY": "sk-dummy"},
            executable="true",
        )
        with pytest.raises(CodexSessionError) as trouble:
            _ = session.prepare()
        assert "OPENAI_API_KEY" in str(trouble.value)


class TestIT070002:
    """AD-070-002: フックの入出力と未完の発話を adapter/tool へ閉じる。"""

    def test_IT_070_002_通常Stopで一往復が確定し再送では増えない(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")

        session_id = "sess-1"
        turn_id = "turn-1"

        # 1. UserPromptSubmit で発話を受け取る
        assert (
            _hook(
                "user-prompt-submit",
                {"session_id": session_id, "turn_id": turn_id, "prompt": "トマトを植えました"},
                run_dir,
                home,
                monkeypatch,
            )
            == 0
        )
        context = capsys.readouterr().out.strip()
        assert len(context.encode("utf-8")) <= 9000
        assert "わたしはそらです" in context and "【気持ち】" in context

        # 2. Stop で返事と気持ちを受け取る
        stopped = {
            "session_id": session_id,
            "turn_id": turn_id,
            "last_assistant_message": "育つのが楽しみですね。\n【気持ち】 +0.2 楽しみ",
        }
        assert _hook("stop", stopped, run_dir, home, monkeypatch) == 0

        # 記憶に1件保存されたか確認
        memories = SqliteMemories(home / "memories.sqlite")
        try:
            assert memories.count_episodes("sora") == 1
            episodes = memories.recent("sora", 1)
            assert episodes[0].utterance == "トマトを植えました"
            assert "育つのが楽しみですね。" in episodes[0].reply
            assert episodes[0].source == f"codex:{turn_id}"
        finally:
            memories.close()

        # 3. 同じ Stop の再送
        assert _hook("stop", stopped, run_dir, home, monkeypatch) == 0

        # 再送で記憶が増えていないことを確認
        memories2 = SqliteMemories(home / "memories.sqlite")
        try:
            assert memories2.count_episodes("sora") == 1
        finally:
            memories2.close()

        # 4. 異なる返事の再送はブロックされる
        changed = {
            **stopped,
            "last_assistant_message": "育つのが楽しみですね。\n【気持ち】 -0.2 違う動き",
        }
        assert _hook("stop", changed, run_dir, home, monkeypatch) == 0
        assert "以前と異なる返事または気持ち" in capsys.readouterr().out

    def test_IT_070_002_Interruptで未完発話が破棄される(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")

        store = PendingStore(run_dir)
        store.save(
            PendingTurn(
                session_id="sess-interrupt",
                turn_id="turn-interrupt",
                utterance="途中で止める質問",
                recalled_at=datetime.now(UTC),
                identity_version=1,
                context="context",
            )
        )
        assert store.read("sess-interrupt") is not None

        assert _hook("interrupt", {"session_id": "sess-interrupt"}, run_dir, home, monkeypatch) == 0
        assert store.read("sess-interrupt") is None

    def test_IT_070_002_SubagentStopは個別の一往復として保存しない(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")

        payload = {
            "session_id": "sess-sub",
            "turn_id": "turn-sub",
            "subagent": True,
            "last_assistant_message": "下位エージェントの返事",
        }
        assert _hook("stop", payload, run_dir, home, monkeypatch) == 0

        memories = SqliteMemories(home / "memories.sqlite")
        try:
            assert memories.count_episodes("sora") == 0
        finally:
            memories.close()

    def test_IT_070_002_compact通知で名乗りと状態を戻す(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")

        assert (
            _hook(
                "session-start",
                {"session_id": "sess-compact", "reason": "compact"},
                run_dir,
                home,
                monkeypatch,
            )
            == 0
        )
        out = capsys.readouterr().out.strip()
        parsed = _mapping(json.loads(out))  # pyright: ignore[reportAny]
        inner = _mapping(parsed["hookSpecificOutput"])
        assert "additionalContext" in inner
        assert "わたしはそらです" in str(inner["additionalContext"])

    def test_IT_070_002_古い未施錠セッションだけを片付ける(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        sessions.mkdir()

        # 1. 24時間以上前の古い未施錠セッション
        old_dir = sessions / "old-session"
        old_dir.mkdir()
        _ = (old_dir / "session.lock").write_text("", encoding="utf-8")
        past = (datetime.now(UTC) - timedelta(hours=25)).timestamp()
        os.utime(old_dir, (past, past))

        # 2. 新しいセッション
        new_dir = sessions / "new-session"
        new_dir.mkdir()
        _ = (new_dir / "session.lock").write_text("", encoding="utf-8")

        StaleSessionCleaner.clean(sessions, age=timedelta(hours=24))

        assert not old_dir.exists()
        assert new_dir.exists()


class TestIT070003:
    """AD-070-003: 会話の口が起動準備、時刻、名乗りの版、再送の整合を持つ。"""

    def test_IT_070_003_保存先で思い出すと覚えるが一取引で確定する(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        startup = Startup(home, default=fixed(CharacterPairs()))
        memories = SqliteMemories(home / "memories.sqlite")
        try:
            settings = SettingsFile(home).read()
            startup.settle(memories, settings)
            conversation = startup.conversation(memories, settings)
            recollection = conversation.recall("sora", "好きな花は何ですか？")
            assert "園芸" in recollection.identity.text

            turn = conversation.remember(
                "sora",
                "好きな花は何ですか？",
                "向日葵が好きです。",
                recalled_at=recollection.recalled_at,
                source="codex:test-turn-1",
                identity_version=recollection.identity.version,
            )
            assert turn.source == "codex:test-turn-1"
        finally:
            memories.close()


class TestIT070004:
    """AD-070-004: 名乗りと思い出しを Codex が読む長さに組む。"""

    def test_IT_070_004_Codexフックへの追加文脈が九千バイト以内に収まる(self) -> None:
        words = CodexWords()
        state = State(Mood(0.0), Character(0.0))
        identity = Identity(version=1, text="そらです。" * 50)
        recollection = Recollection(
            identity=identity,
            state=state,
            found=tuple(
                Found(
                    episode=Episode(
                        id=i,
                        identity_version=1,
                        happened_at=datetime(2025, 1, 1, tzinfo=UTC),
                        utterance=f"長い発話-{i}" * 20,
                        reply=f"長い返事-{i}" * 20,
                    ),
                    relevance=0.8,
                    retrieval=Retrieval(0, None),
                    way="characters",
                )
                for i in range(50)
            ),
            recent=(),
            dream=None,
            recalled_at=datetime(2025, 1, 2, tzinfo=UTC),
        )

        response_json = words.hook_response(recollection, limit=9000)
        assert len(response_json.encode("utf-8")) <= 9000
        parsed = _mapping(json.loads(response_json))  # pyright: ignore[reportAny]
        inner = _mapping(parsed["hookSpecificOutput"])
        assert "additionalContext" in inner
        assert "省略" in str(inner["additionalContext"])

    def test_IT_070_004_返事末尾の気持ちの印を本文と分離する(self) -> None:
        words = CodexWords()
        spoken = words.parted("答えです。\n【気持ち】 +0.5 嬉しいな")
        assert spoken.reply == "答えです。"
        assert spoken.moved.delta == 0.5
        assert spoken.moved.cause == "嬉しいな"

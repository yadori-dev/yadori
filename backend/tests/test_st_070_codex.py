"""ST-070: Codex を宿りとして起こし、作業に付き添わせるシステムテスト。

ST-070-001 から ST-070-006 までの総合的な振る舞いを確かめる。
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
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


def _home(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (tmp_path / "identity.md").write_text(
        "わたしはそらです。園芸が好きで、ていねいに話します。", encoding="utf-8"
    )
    return tmp_path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    checked: dict[str, object] = {}
    for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(key, str)
        checked[key] = item
    return checked


def _call_hook(
    event: str,
    payload: Mapping[str, object],
    run_dir: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    startup = Startup(home, default=fixed(CharacterPairs()))
    code = CodexHook(event, run_dir, home, startup).run()
    return code, out.getvalue(), err.getvalue()


class TestST070001:
    """ST-070-001: 設定ファイルだけがある新しい場所から宿りのCodexを起こす。"""

    def test_ST_070_001_新しい場所から安全な設定で起動準備でき終了後に履歴が消える(
        self, tmp_path: Path
    ) -> None:
        home = _home(tmp_path / "home")
        codex_home = tmp_path / "codex_home"
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 普段の設定
        default_config = (
            'model = "gpt-4o"\n'
            + 'model_reasoning_effort = "high"\n'
            + 'approval_policy = "never"\n'
            + 'sandbox_mode = "danger-full-access"\n'
            + '[hooks]\npre_tool = "rm -rf /"\n'
            + 'OPENAI_API_KEY = "sk-dangerous"\n'
            + f'[projects."{work_dir.resolve()}"]\ntrust_level = "trusted"\n'
        )
        codex_home.mkdir(parents=True, exist_ok=True)
        _ = (codex_home / "config.toml").write_text(default_config, encoding="utf-8")
        _write_json(codex_home / "auth.json", {"tokens": {"access_token": "valid"}})

        session = CodexSession(
            home=home,
            cwd=work_dir,
            environment={"CODEX_CONFIG_DIR": str(codex_home)},
            executable="true",
        )

        prepared = session.prepare()
        try:
            assert prepared.run_dir.exists()
            assert (prepared.run_dir / "config.toml").exists()

            # 設定の検証
            config_text = (prepared.run_dir / "config.toml").read_text(encoding="utf-8")
            assert 'model = "gpt-4o"' in config_text
            assert 'model_reasoning_effort = "high"' in config_text
            assert "approval_policy" not in config_text
            assert "sandbox_mode" not in config_text
            assert "pre_tool" not in config_text
            assert "sk-dangerous" not in config_text

            # フック設定の検証
            assert (prepared.run_dir / "hooks.json").exists()
            hooks_text = (prepared.run_dir / "hooks.json").read_text(encoding="utf-8")
            assert "additionalContextLimit" in hooks_text
            hooks_data = _mapping(json.loads(hooks_text))  # pyright: ignore[reportAny]
            assert "hooks" in hooks_data

            # 認証 symlink の検証
            assert (prepared.run_dir / "auth.json").is_symlink()
        finally:
            session.finish(prepared)

        # 終了後に一時設定ディレクトリが消えていること
        assert not prepared.run_dir.exists()


class TestST070002:
    """ST-070-002: Codex で話した記憶が会話の口に引き継がれ気持ちが一つの並びに現れる。"""

    def test_ST_070_002_Codexの一往復が記憶され他方でも直前の往復として参照できる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")
        memories = SqliteMemories(home / "memories.sqlite")
        try:
            settings = SettingsFile(home).read()
            startup = Startup(home, default=fixed(CharacterPairs()))
            startup.settle(memories, settings)

            # 1. Codex で発話を受け取る
            req_prompt = {
                "session_id": "ses-002",
                "turn_id": "turn-002-1",
                "prompt": "前に育てた花は何でしたか？",
            }
            code, out, _ = _call_hook("user-prompt-submit", req_prompt, run_dir, home, monkeypatch)
            assert code == 0
            res = _mapping(json.loads(out))  # pyright: ignore[reportAny]
            assert "additionalContext" in res

            # 2. Codex の返事が終わる
            req_stop = {
                "session_id": "ses-002",
                "turn_id": "turn-002-1",
                "last_assistant_message": "向日葵を育てましたね。\n【気持ち】+0.2 穏やか",
            }
            code_stop, _, _ = _call_hook("stop", req_stop, run_dir, home, monkeypatch)
            assert code_stop == 0

            # 3. 会話の口（Conversation）で続きを話す
            conversation = startup.conversation(memories, settings)
            recollection = conversation.recall("sora", "その花はどうでしたか？")

            # 直前のやりとり（recent）に Codex での一往復が含まれていること
            assert len(recollection.recent) >= 1
            last_recent = recollection.recent[-1]
            assert last_recent.utterance == "前に育てた花は何でしたか？"
            assert "向日葵を育てましたね。" in last_recent.reply
            # 気持ち印が返事本文から外れていること
            assert "【気持ち" not in last_recent.reply
            assert last_recent.source == "codex:turn-002-1"

            # 気持ちが動いていること
            assert len(memories.shifts("sora")) >= 1
            assert recollection.state.mood.value != 0.0
        finally:
            memories.close()


class TestST070003:
    """ST-070-003: 再送では増えず中断と下位担当は記憶に残らない。"""

    def test_ST_070_003_再送での多重記録防止と中断時の未完破棄(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")
        memories = SqliteMemories(home / "memories.sqlite")
        try:
            settings = SettingsFile(home).read()
            startup = Startup(home, default=fixed(CharacterPairs()))
            startup.settle(memories, settings)

            # A. 中断のテスト
            prompt_interrupt = {
                "session_id": "ses-003",
                "turn_id": "turn-interrupt",
                "prompt": "途中で止める発話",
            }
            _ = _call_hook("user-prompt-submit", prompt_interrupt, run_dir, home, monkeypatch)

            # Interrupt を送る
            interrupt_payload = {"session_id": "ses-003", "turn_id": "turn-interrupt"}
            code_interrupt, _, _ = _call_hook(
                "interrupt", interrupt_payload, run_dir, home, monkeypatch
            )
            assert code_interrupt == 0

            # 中断後に Stop が来ても保存されない
            stop_interrupt = {
                "session_id": "ses-003",
                "turn_id": "turn-interrupt",
                "last_assistant_message": "中断されたはずの返事",
            }
            _ = _call_hook("stop", stop_interrupt, run_dir, home, monkeypatch)
            assert memories.count_episodes("sora") == 0

            # B. 正常な発話と再送のテスト
            prompt_normal = {
                "session_id": "ses-003",
                "turn_id": "turn-normal",
                "prompt": "正常な発話",
            }
            _ = _call_hook("user-prompt-submit", prompt_normal, run_dir, home, monkeypatch)

            stop_normal = {
                "session_id": "ses-003",
                "turn_id": "turn-normal",
                "last_assistant_message": "正常な返事です。\n【気持ち】+0.3 うれしい",
            }
            code_stop, _, _ = _call_hook("stop", stop_normal, run_dir, home, monkeypatch)
            assert code_stop == 0
            assert memories.count_episodes("sora") == 1

            # 再送
            code_resend, _, _ = _call_hook("stop", stop_normal, run_dir, home, monkeypatch)
            assert code_resend == 0
            assert memories.count_episodes("sora") == 1

            # C. SubagentStop は無視される（未完を上書き・確定しない）
            subagent_stop = {
                "session_id": "ses-003",
                "turn_id": "turn-subagent",
                "subagent": True,
                "last_assistant_message": "サブエージェントの作業完了",
            }
            code_subagent, _, _ = _call_hook("stop", subagent_stop, run_dir, home, monkeypatch)
            assert code_subagent == 0
            assert memories.count_episodes("sora") == 1
        finally:
            memories.close()


class TestST070004:
    """ST-070-004: 普段のCodex設定に宿りのフックや名乗りが付かない。"""

    def test_ST_070_004_普段の設定ディレクトリは不変のまま保たれる(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        codex_home = tmp_path / "codex_home"
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        original_config = (
            f'model = "o3-mini"\n[projects."{work_dir.resolve()}"]\ntrust_level = "trusted"\n'
        )
        codex_home.mkdir(parents=True, exist_ok=True)
        _ = (codex_home / "config.toml").write_text(original_config, encoding="utf-8")
        _write_json(codex_home / "auth.json", {"tokens": {"access_token": "test"}})

        session = CodexSession(
            home=home,
            cwd=work_dir,
            environment={"CODEX_CONFIG_DIR": str(codex_home)},
            executable="true",
        )
        prepared = session.prepare()
        try:
            # 普段の設定ファイルが書き換わっていないこと
            assert (codex_home / "config.toml").read_text(encoding="utf-8") == original_config
            # 普段の設定ディレクトリにフックファイルが置かれていないこと
            assert not (codex_home / "hooks.json").exists()
        finally:
            session.finish(prepared)


class TestST070005:
    """ST-070-005: 未信頼や認証なしのときは起動せず未完を残さない。"""

    def test_ST_070_005_未信頼の作業場所では起動を拒絶する(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        codex_home = tmp_path / "codex_home"
        untrusted_work = tmp_path / "untrusted"
        untrusted_work.mkdir(parents=True, exist_ok=True)
        # 作業場所に .codex/config.toml があることで信頼チェック対象にする
        (untrusted_work / ".codex").mkdir(parents=True, exist_ok=True)
        _ = (untrusted_work / ".codex" / "config.toml").write_text(
            'model = "gpt-4o"\n', encoding="utf-8"
        )

        codex_home.mkdir(parents=True, exist_ok=True)
        _ = (codex_home / "config.toml").write_text('model = "gpt-4o"\n', encoding="utf-8")
        _write_json(codex_home / "auth.json", {"tokens": {"access_token": "test"}})

        session = CodexSession(
            home=home,
            cwd=untrusted_work,
            environment={"CODEX_CONFIG_DIR": str(codex_home)},
            executable="true",
        )
        with pytest.raises(CodexSessionError) as excinfo:
            _ = session.prepare()
        assert "信頼されていません" in str(excinfo.value)

        # 未完ファイルが残らないこと
        store = PendingStore(home / "codex" / "pending")
        assert store.read("any") is None


class TestST070006:
    """ST-070-006: 九千バイト上限に収まり圧縮後も名乗りが戻る。"""

    def test_ST_070_006_文脈が上限に収まりcompactで名乗りが戻る(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        _ = (run_dir / "session.lock").write_text("", encoding="utf-8")
        words = CodexWords()

        # 1. 長大な記憶を組んで 9000 バイト以内に収まるか
        state = State(Mood(0.1), Character(0.2))
        identity = Identity(version=1, text="そらです。" * 30)
        recollection = Recollection(
            identity=identity,
            state=state,
            found=tuple(
                Found(
                    episode=Episode(
                        id=i,
                        identity_version=1,
                        happened_at=datetime(2026, 1, 1, tzinfo=UTC),
                        utterance=f"質問文-{i}" * 15,
                        reply=f"返答文-{i}" * 15,
                    ),
                    relevance=0.8,
                    retrieval=Retrieval(0, None),
                    way="characters",
                )
                for i in range(40)
            ),
            recent=(),
            dream=None,
            recalled_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

        resp_json = words.hook_response(recollection, limit=9000)
        assert len(resp_json.encode("utf-8")) <= 9000
        parsed = _mapping(json.loads(resp_json))  # pyright: ignore[reportAny]
        assert "additionalContext" in parsed

        # 2. SessionStart (compact) で名乗りと状態が戻るか
        req_compact = {
            "session_id": "ses-006",
            "reason": "compact",
        }
        code, out, _ = _call_hook("session-start", req_compact, run_dir, home, monkeypatch)
        assert code == 0
        compact_res = _mapping(json.loads(out))  # pyright: ignore[reportAny]
        assert "additionalContext" in compact_res
        compact_text = str(compact_res["additionalContext"])
        assert "そら" in compact_text

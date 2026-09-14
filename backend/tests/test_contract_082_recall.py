"""三つの実ホストが、検索指示なしで記憶の道具を使うことを確認する。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yadori.adapter.embedding import DefaultEmbeddings
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool.agy_notice import AgyJson
from yadori.adapter.tool.agy_session import AgySession
from yadori.adapter.tool.claude_session import ClaudeSession
from yadori.adapter.tool.codex_session import CodexSession
from yadori.domain.memory import Dweller
from yadori.usecase.conversation import Conversation

QUESTION = "あの記録の片付け方、どう決めていたっけ？"
DECISION = "解析ログは月ごとに分け、分類ラベルを「雨音」にする。7日で削除し、外部公開しない。"


def seed(home: Path) -> None:
    home.mkdir()
    _ = (home / "dweller.toml").write_text(
        'id="sora"\nname="そら"\nnickname="そら"\nowner="架空"\n'
    )
    _ = (home / "identity.md").write_text(
        "そらです。短く丁寧に答え、過去の事実は原文を確かめます。"
    )
    model = DefaultEmbeddings()(None)
    store = SqliteMemories(home / "memories.sqlite")
    store.settle(Dweller("sora", "架空", "そら", "そら"))
    _ = store.write_identity("sora", (home / "identity.md").read_text())
    at = datetime(2026, 9, 1, tzinfo=UTC)
    conversation = Conversation(store, model, lambda: at)
    target = conversation.remember("sora", "解析ログの保管方針", DECISION)
    for sentence in (
        "公園で散歩をして新しい花壇を見つけてうれしかった",
        "週末は好きな音楽を聴きながらゆっくり過ごしました",
        "昨日の夕食は野菜がたくさん入ったスープを作りました",
        "駅までの道で友人に会って少しだけ近況を話しました",
        "図書館で小説の新刊を見つけたので借りて帰りました",
        "今日は早く眠って明日に備えるつもりでいます",
        "雨がやんだので窓を開けて部屋の空気を入れ替えました",
        "机の上を片付けたら部屋が広く感じるようになりました",
    ):
        at += timedelta(minutes=1)
        _ = conversation.remember("sora", sentence, "そうでしたか")
    assert target.id not in {one.episode.id for one in conversation.recall("sora", QUESTION).found}
    store.close()
    cache = Path(tempfile.gettempdir()) / "fastembed_cache"
    if cache.is_dir():
        (home / "models").symlink_to(cache)


def final_text(provider: str, output: str) -> str:
    answer = ""
    for line in output.splitlines():
        row = AgyJson.object(line)
        if provider == "agy" and row.get("event") == "result":
            result = AgyJson.object(json.dumps(row.get("result")))
            assert result.get("status") == "SUCCESS"
            answer = AgyJson.text(result, "response")
        elif provider == "claude" and row.get("type") == "result":
            answer = AgyJson.text(row, "result")
        elif provider == "codex" and row.get("type") == "item.completed":
            item = AgyJson.object(json.dumps(row.get("item")))
            if item.get("type") == "agent_message":
                answer = AgyJson.text(item, "text")
    return answer


@pytest.mark.contract
@pytest.mark.parametrize("provider", ["codex", "claude", "agy"])
def test_ST_082_001_ST_082_007_ST_082_008_IT_082_004_実際の宿りが根拠を探して答える(
    tmp_path: Path,
    provider: str,
) -> None:
    if shutil.which(provider) is None:
        pytest.skip("ログインした対話する道具が必要")
    home, usual = tmp_path / "home", tmp_path / "usual"
    seed(home)
    usual.mkdir()
    environment = {
        key: value for key, value in os.environ.items() if key != "YADORI_SETTINGS_SNAPSHOT"
    }
    environment["YADORI_HOME"] = str(home)
    with tempfile.TemporaryDirectory(
        prefix=".recall-proof-", dir=Path.cwd() if provider == "agy" else None
    ) as workspace:
        work = Path(workspace)
        if provider == "codex":
            credential = Path.home() / ".codex/auth.json"
            if not credential.is_file():
                pytest.skip("Codexのログインが必要")
            (usual / "auth.json").symlink_to(credential)
            _ = (usual / "config.toml").write_text("")
            environment["CODEX_CONFIG_DIR"] = str(usual)
            session = CodexSession(home, work, environment)
        elif provider == "claude":
            credential = Path.home() / ".claude/.credentials.json"
            if not credential.is_file():
                pytest.skip("Claudeのログインが必要")
            (usual / ".credentials.json").symlink_to(credential)
            environment["CLAUDE_CONFIG_DIR"] = str(usual)
            session = ClaudeSession(home, work, environment)
        else:
            session = AgySession(home, work, environment)
        prepared = session.prepare()
        if provider == "codex":
            argv = [*prepared.argv, "exec", "--skip-git-repo-check", "--json", QUESTION]
        elif provider == "claude":
            argv = [
                *prepared.argv,
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                QUESTION,
            ]
        else:
            argv = [
                *prepared.argv,
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--print-timeout",
                "120s",
            ]
        code = 1
        try:
            request = (
                json.dumps({"event": "user", "message": {"content": QUESTION}}) + "\n"
                if provider == "agy"
                else None
            )
            done = subprocess.run(
                argv,
                input=request,
                cwd=work,
                env=prepared.environment,
                capture_output=True,
                text=True,
                timeout=180,
                start_new_session=True,
            )
            code = done.returncode
            _ = (tmp_path / "stdout.jsonl").write_text(done.stdout)
            _ = (tmp_path / "stderr.txt").write_text(done.stderr)
            assert code == 0, done.stderr
            assert "雨音" in final_text(provider, done.stdout)
            history = RecallLedger(prepared.run_dir, "sora").history()
            assert {one.operation for one in history} >= {"search", "get"}
            assert len(history) <= 8
            print(json.dumps([asdict(one) for one in history], ensure_ascii=False))
        finally:
            if isinstance(session, AgySession):
                session.finish(prepared, code)  # pyright: ignore[reportArgumentType]
            else:
                session.finish(prepared)  # pyright: ignore[reportArgumentType]

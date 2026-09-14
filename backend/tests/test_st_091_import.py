"""通常ログを確認・保存・再読し、人物と原文を変えず使えることを確かめる。"""

from __future__ import annotations

import io
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.importing.archive import SqliteArchive
from yadori.adapter.importing.records import RecordJson, SessionLogs
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ClaudeWords, CodexWords
from yadori.adapter.tool.agy_words import AgyWords
from yadori.domain.importing.model import ImportFailed, Provider
from yadori.domain.memory import Dweller, HowToRecall
from yadori.infrastructure.importing import Importer, ImportSource
from yadori.infrastructure.memory_mcp import MemoryTools
from yadori.usecase.conversation import Conversation
from yadori.usecase.importing.service import Importing
from yadori.usecase.recall.service import Reading

FIXTURES = Path(__file__).parent / "fixtures/import-091"
PAIRS = CharacterPairs()
AT = datetime(2026, 9, 14, tzinfo=UTC)


def parsed(text: str) -> dict[str, object]:
    value: object = json.loads(text)  # pyright: ignore[reportAny]
    return RecordJson.mapping(value)


def source(provider: Provider) -> Path:
    return (
        FIXTURES / (provider + ".jsonl")
        if provider != "agy"
        else FIXTURES / "agy-session/.system_generated/logs/transcript_full.jsonl"
    )


def settings(home: Path) -> None:
    home.mkdir(exist_ok=True)
    _ = (home / "settings.toml").write_text(
        'version=1\nactive="a"\n[people.a]\nname="そら"\nnickname="そら"\nowner="架空"\nidentity="そらです"\n[people.b]\nname="別人"\nnickname="別人"\nowner="架空"\nidentity="別人です"\n'
    )


@pytest.mark.parametrize("provider", ["claude", "codex", "agy"])
def test_ST_091_001_ST_091_005_IT_091_001_確認は書かず通常ログを原文で取り込む(
    tmp_path: Path, provider: Provider
) -> None:
    settings(tmp_path)
    output = io.StringIO()
    importer = Importer(tmp_path, output, lambda _p, _a: PAIRS)
    src = ImportSource(provider, source(provider))
    original = src.path.read_bytes()
    assert importer.run([src], "a") == 0
    assert not (tmp_path / "memories.sqlite").exists()
    assert "追加予定: 1 件" in output.getvalue()
    assert importer.run([src], "a", True) == 0
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    record = SessionLogs().read(provider, src.path).conversations[0]
    kept = archive.existing("a", record.source)
    assert kept is not None and kept.conversation == record
    assert archive.existing("b", record.source) is None
    assert importer.run([src], "a", True) == 0
    assert "追加予定: 0 件 / 既存: 1 件" in output.getvalue()
    assert src.path.read_bytes() == original
    with sqlite3.connect(tmp_path / "memories.sqlite") as conn:
        rows: list[tuple[str]] = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {row[0] for row in rows}
    assert tables == {"external_conversation", "external_index"}


def test_ST_091_003_IT_091_002_追記と同番号別会話と競合(tmp_path: Path) -> None:
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    importing = Importing(archive, "a")
    first = SessionLogs().read("agy", source("agy")).conversations[0]
    assert importing.apply(importing.preview([first]), PAIRS) == 1
    before = archive.existing("a", first.source)
    second = replace(first, turn="2", answer="3", utterance="次の決定", previous=first.turn)
    other = replace(first, session="another")
    assert importing.apply(importing.preview([first, second, other]), PAIRS) == 2
    assert archive.existing("a", first.source) == before
    assert len(archive.following("a", first)) == 1
    assert importing.apply(importing.preview([first, second, other]), PAIRS) == 0
    with pytest.raises(ImportFailed, match="原文が変わ"):
        _ = importing.preview([replace(first, reply="異なる返事")])
    assert archive.existing("a", first.source) == before


def test_ST_091_003_IT_091_002_索引失敗でも一往復単位で原文と索引が揃う(tmp_path: Path) -> None:
    path = tmp_path / "memories.sqlite"
    archive = SqliteArchive(path)
    importing = Importing(archive, "a")
    first = SessionLogs().read("agy", source("agy")).conversations[0]
    second = replace(first, turn="2", answer="3")

    def stop_second(saved: int, _total: int) -> None:
        if saved == 1:
            with sqlite3.connect(path) as connection:
                _ = connection.execute(
                    "CREATE TRIGGER fail_index BEFORE INSERT ON external_index "
                    + "BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
                )

    with pytest.raises(sqlite3.IntegrityError):
        _ = importing.apply(importing.preview([first, second]), PAIRS, progress=stop_second)
    assert importing.saved == 1
    assert archive.existing("a", first.source) is not None
    assert archive.existing("a", second.source) is None
    with sqlite3.connect(path) as connection:
        _ = connection.execute("DROP TRIGGER fail_index")
    assert importing.apply(importing.preview([first, second]), PAIRS) == 1


def test_ST_091_004_ST_091_006_IT_091_003_自動提示とMCPが外部出典を保つ(tmp_path: Path) -> None:
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    record = SessionLogs().read("claude", source("claude")).conversations[0]
    importing = Importing(archive, "a")
    _ = importing.apply(importing.preview([record]), PAIRS)
    memories = SqliteMemories(tmp_path / "memories.sqlite")
    try:
        for person in ["a", "b"]:
            memories.settle(Dweller(person, "架空", person, person))
            _ = memories.write_identity(person, person + "です")
        conversation = Conversation(
            memories, PAIRS, lambda: AT, HowToRecall(found_limit=3), archive
        )
        own = conversation.remember("a", record.utterance, "自身の会話です")
        state = conversation.state("a")
        recalled = conversation.recall("a", record.utterance)
        assert recalled.external and recalled.recent[0].id == own.id
        for words in (ClaudeWords(), CodexWords(), AgyWords()):
            assert "外部会話" in words.hook_response(recalled)
            assert "宿り自身の発言ではありません" in words.preface(recalled)
        assert not conversation.recall("b", record.utterance).external
        assert state == conversation.state("a")
        ledger = RecallLedger(tmp_path, "a")
        turn = ledger.begin("one", ())
        tools = MemoryTools(ledger, Reading(memories, PAIRS, "a", archive))
        result = parsed(tools.search(turn, record.utterance))
        candidates = RecordJson.objects(result["candidates"])
        assert len(candidates) == 2
        references = [str(row["reference"]) for row in candidates]
        assert references[0] != references[1]
        pages = [parsed(tools.get(turn, reference)) for reference in references]
        assert any("外部のclaude" in str(page.get("text")) for page in pages)
        assert any("自身の会話です" in str(page.get("text")) for page in pages)
        assert not archive.search("b", PAIRS.name, PAIRS.to_recall(record.utterance), 0, 3)
    finally:
        memories.close()


def test_ST_091_007_途中の破損は全件停止し末尾だけの未完は知らせる(tmp_path: Path) -> None:
    settings(tmp_path)
    folder = tmp_path / "logs"
    folder.mkdir()
    _ = (folder / "one.jsonl").write_bytes(source("claude").read_bytes())
    _ = (folder / "broken.jsonl").write_text("{broken}\n")
    output = io.StringIO()
    importer = Importer(tmp_path, output, lambda _p, _a: PAIRS)
    assert importer.run([ImportSource("claude", folder)], "a", True) == 1
    assert not (tmp_path / "memories.sqlite").exists()
    partial = tmp_path / "partial.jsonl"
    _ = partial.write_bytes(source("claude").read_bytes() + b'{"partial":')
    assert importer.run([ImportSource("claude", partial)], "a", True) == 0
    assert "末尾の書きかけ" in output.getvalue()


def test_ST_091_007_宿り自身の出典は外部へ重複保存しない(tmp_path: Path) -> None:
    record = SessionLogs().read("codex", source("codex")).conversations[0]
    memories = SqliteMemories(tmp_path / "memories.sqlite")
    try:
        memories.settle(Dweller("a", "架空", "そら", "そら"))
        _ = memories.write_identity("a", "そらです")
        _ = Conversation(memories, PAIRS, lambda: AT).remember(
            "a", record.utterance, record.reply, source=f"codex:{record.turn}"
        )
        archive = SqliteArchive(tmp_path / "memories.sqlite")
        plan = Importing(archive, "a").preview([record])
        assert plan.native == 1 and not plan.added
        assert archive.existing("a", record.source) is None
    finally:
        memories.close()


@pytest.mark.parametrize("provider", ["claude", "codex", "agy"])
def test_ST_091_002_IT_091_001_途中応対を完成した原文にしない(
    tmp_path: Path, provider: Provider
) -> None:
    rows, _ = RecordJson.rows(source(provider))
    if provider == "claude":
        for row in rows:
            if row.get("type") == "assistant":
                message = RecordJson.mapping(row.get("message"))
                message["stop_reason"] = "tool_use"
                row["message"] = message
        destination = tmp_path / "unfinished.jsonl"
    elif provider == "codex":
        rows = [
            row
            for row in rows
            if RecordJson.mapping(row.get("payload")).get("type") != "task_complete"
        ]
        destination = tmp_path / "unfinished.jsonl"
    else:
        rows[-1]["tool_calls"] = [{"name": "read_file"}]
        destination = tmp_path / "session/.system_generated/logs/transcript_full.jsonl"
        destination.parent.mkdir(parents=True)
    _ = destination.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    )
    result = SessionLogs().read(provider, destination)
    assert not result.conversations and result.notices


def test_ST_091_010_起動一回の取込上限で残りを再実行できる(tmp_path: Path) -> None:
    record = SessionLogs().read("agy", source("agy")).conversations[0]
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    importing = Importing(archive, "a")
    records = [replace(record, turn=str(index), answer=str(index + 1000)) for index in range(201)]
    assert importing.apply(importing.preview(records), PAIRS, 200) == 200
    plan = importing.preview(records)
    assert len(plan.added) == 1 and plan.existing == 200
    assert importing.apply(plan, PAIRS, 200) == 1


@pytest.mark.parametrize(
    "origin", [{"subagent": {"thread_spawn": {"parent_thread_id": "fixture"}}}, "subagent"]
)
def test_ST_091_002_通常の場所にあるCodex子担当ログも除外する(
    tmp_path: Path, origin: object
) -> None:
    rows, _ = RecordJson.rows(source("codex"))
    for row in rows:
        if row.get("type") == "session_meta":
            payload = RecordJson.mapping(row["payload"])
            payload["source"] = origin
            row["payload"] = payload
    path = tmp_path / "rollout-child.jsonl"
    _ = path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = SessionLogs().read("codex", path)
    assert not result.conversations and "子担当" in str(result.notices)


def test_ST_091_002_未完往復を飛び越えて先行文脈にしない(tmp_path: Path) -> None:
    rows, _ = RecordJson.rows(source("agy"))
    first, answer = rows
    unfinished = dict(first)
    unfinished["step_index"] = 2
    later = dict(first)
    later["step_index"] = 3
    end = dict(answer)
    end["step_index"] = 4
    path = tmp_path / "agy-session/.system_generated/logs/transcript_full.jsonl"
    path.parent.mkdir(parents=True)
    _ = path.write_text(
        "\n".join(json.dumps(row) for row in [first, answer, unfinished, later, end]) + "\n"
    )
    result = SessionLogs().read("agy", path)
    assert len(result.conversations) == 2
    assert result.conversations[1].previous is None


@pytest.mark.parametrize("image_only", [False, True])
def test_ST_091_002_Codexの未完と画像を越えて先行を結ばない(
    tmp_path: Path, image_only: bool
) -> None:
    rows, _ = RecordJson.rows(source("codex"))
    first = SessionLogs().read("codex", source("codex")).conversations[0]
    started = next(
        row for row in rows if RecordJson.mapping(row.get("payload")).get("type") == "task_started"
    )
    human = next(
        row
        for row in rows
        if RecordJson.parts(RecordJson.mapping(row.get("payload")).get("content"))
        == first.utterance
    )
    middle: object = json.loads(json.dumps([started, human]).replace(first.turn, "unfinished"))  # pyright: ignore[reportAny]
    pending = RecordJson.objects(middle)
    if image_only:
        payload = RecordJson.mapping(pending[1]["payload"])
        payload["content"] = [{"type": "input_image", "image_url": "fixture"}]
        pending[1]["payload"] = payload
    last: object = json.loads(json.dumps(rows).replace(first.turn, "later-turn"))  # pyright: ignore[reportAny]
    combined = [*rows, *pending, *RecordJson.objects(last)]
    path = tmp_path / "rollout.jsonl"
    _ = path.write_text("\n".join(json.dumps(row) for row in combined) + "\n")
    result = SessionLogs().read("codex", path)
    assert len(result.conversations) == 2
    assert result.conversations[-1].previous is None

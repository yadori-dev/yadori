"""Claudeのスキル付加文と分割応対を、原文と互換性を保って取り込む。"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.importing.archive import SqliteArchive
from yadori.adapter.importing.records import ClaudeRecords, RecordJson
from yadori.domain.importing.model import ExternalConversation
from yadori.infrastructure.importing import Importer, ImportSource
from yadori.usecase.importing.service import Importing

FIXTURE = Path(__file__).parent / "fixtures/import-091/claude-skill.jsonl"


def sample() -> list[dict[str, object]]:
    rows, _ = RecordJson.rows(FIXTURE)
    return rows


def test_ST_091_002_ST_091_007_IT_091_001_思考とスキル説明を本文へ混ぜない() -> None:
    result = ClaudeRecords().read(sample())
    assert len(result.conversations) == 1
    record = result.conversations[0]
    assert record.utterance.startswith("Use the import-repro skill.")
    assert "IMPORT_SKILL_OK" in record.reply
    assert "Base directory" not in record.reply and not result.notices


@pytest.mark.parametrize(
    "missing", ["isMeta", "sourceToolUseID", "prefix", "session", "sidechain", "image", "interrupt"]
)
def test_ST_091_002_IT_091_001_不完全な付加文や別の発話を飛び越さない(missing: str) -> None:
    rows = sample()
    meta = next(row for row in rows if row.get("isMeta") is True)
    if missing in {"isMeta", "sourceToolUseID"}:
        del meta[missing]
    elif missing == "prefix":
        meta["message"] = {"role": "user", "content": "未知の付加文"}
    elif missing == "session":
        meta["sessionId"] = "another-session"
    elif missing == "sidechain":
        meta["isSidechain"] = True
    else:
        _ = meta.pop("isMeta", None)
        _ = meta.pop("sourceToolUseID", None)
        content: object = (
            [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "fixture"},
                }
            ]
            if missing == "image"
            else "[Request interrupted by user]"
        )
        meta["message"] = {"role": "user", "content": content}
    result = ClaudeRecords().read(rows)
    if missing == "prefix":
        assert len(result.conversations) == 1
    else:
        assert not result.conversations
        assert result.notices


def test_ST_091_007_思考だけなら未完の通知を残す() -> None:
    rows = [
        row
        for row in sample()
        if not (
            row.get("type") == "assistant"
            and RecordJson.mapping(row.get("message")).get("stop_reason") == "end_turn"
            and RecordJson.parts(RecordJson.mapping(row.get("message")).get("content"))
        )
    ]
    result = ClaudeRecords().read(rows)
    assert not result.conversations
    assert len(result.notices) == 1 and "未完" in result.notices[0]


def test_ST_091_003_IT_091_002_旧版の既存記録は変えず復元した分だけ足す(tmp_path: Path) -> None:
    rows = sample()
    first = ClaudeRecords().read(rows).conversations[0]
    final = next(row for row in reversed(rows) if row.get("type") == "assistant")
    user: dict[str, object] = {
        "uuid": "next-user",
        "parentUuid": final["uuid"],
        "sessionId": first.session,
        "type": "user",
        "timestamp": first.happened_at.isoformat(),
        "message": {"role": "user", "content": "次の質問"},
    }
    answer: dict[str, object] = {
        "uuid": "next-answer",
        "parentUuid": "next-user",
        "sessionId": first.session,
        "type": "assistant",
        "timestamp": first.happened_at.isoformat(),
        "message": {
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "次の返事"}],
        },
    }
    rows.extend([user, answer])
    records = ClaudeRecords().read(rows).conversations
    assert len(records) == 2 and records[1].previous is None
    old = ExternalConversation(
        "claude",
        first.session,
        "next-user",
        "next-answer",
        first.happened_at,
        "次の質問",
        "次の返事",
        None,
    )
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    pairs = CharacterPairs()
    _ = archive.keep("person", old, pairs.name, pairs.to_remember(old.utterance))
    before = archive.existing("person", old.source)
    importing = Importing(archive, "person")
    assert importing.apply(importing.preview(records), pairs) == 1
    assert archive.existing("person", old.source) == before
    assert importing.apply(importing.preview(records), pairs) == 0
    assert records[0] == first


def test_ST_091_007_同じ除外理由は発生回数付きの一行にする(tmp_path: Path) -> None:
    rows = sample()
    final = copy.deepcopy(next(row for row in reversed(rows) if row.get("type") == "assistant"))
    rows = [row for row in rows if row.get("uuid") != final["uuid"]]
    for index in range(3):
        orphan = copy.deepcopy(final)
        orphan["uuid"] = f"orphan-{index}"
        orphan["parentUuid"] = "missing"
        rows.append(orphan)
    path = tmp_path / "session.jsonl"
    _ = path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    _ = (tmp_path / "dweller.toml").write_text(
        'id="person"\nname="そら"\nnickname="そら"\nowner="架空"\n'
    )
    _ = (tmp_path / "identity.md").write_text("そらです")
    output = io.StringIO()
    assert Importer(tmp_path, output).run([ImportSource("claude", path)], "person") == 0
    text = output.getvalue()
    assert text.count("完成応対の利用者発話を確認できず除外") == 1
    assert "同じ理由の発生: 3 回" in text and "未完" in text
    assert not (tmp_path / "memories.sqlite").exists()

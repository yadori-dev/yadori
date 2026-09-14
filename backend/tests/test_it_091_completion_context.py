"""実ログで観測した付加文と、完了順が重なる往復の境界。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from yadori.adapter.importing.records import CodexRecords, SessionLogs

AT = "2026-09-14T00:00:00Z"


def user(identifier: str, parent: str | None, text: str) -> dict[str, object]:
    return {
        "type": "user",
        "uuid": identifier,
        "parentUuid": parent,
        "sessionId": "fixture",
        "timestamp": AT,
        "message": {"role": "user", "content": text},
    }


def answer(identifier: str, parent: str, text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "uuid": identifier,
        "parentUuid": parent,
        "sessionId": "fixture",
        "timestamp": AT,
        "message": {
            "id": "message-" + identifier,
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        },
    }


def encoded(rows: list[dict[str, object]]) -> bytes:
    return ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()


def log(path: Path, rows: list[dict[str, object]]) -> Path:
    _ = path.write_bytes(encoded(rows))
    return path


def event(kind: str, turn: str, **extra: object) -> dict[str, object]:
    return {"type": "event_msg", "payload": {"type": kind, "turn_id": turn, **extra}}


def human(turn: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "timestamp": AT,
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "質問" + turn}],
        },
    }


def final(text: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def overlapping() -> list[dict[str, object]]:
    return [
        {"type": "session_meta", "payload": {"id": "fixture"}},
        event("task_started", "t1"),
        human("t1"),
        event("task_started", "t2"),
        human("t2"),
    ]


@pytest.mark.parametrize("order", [("t1", "t2"), ("t2", "t1")])
def test_ST_091_002_IT_091_001_重なる開始と完了を識別子で対応する(order: tuple[str, str]) -> None:
    rows = overlapping()
    for turn in order:
        rows += [final("同じ返事"), event("task_complete", turn, last_agent_message="同じ返事")]
    rows.append(event("task_complete", order[-1], last_agent_message="同じ返事"))
    result = CodexRecords().read(rows)
    assert {one.turn: one.utterance for one in result.conversations} == {
        "t1": "質問t1",
        "t2": "質問t2",
    }
    assert all(one.previous is None for one in result.conversations)
    assert not result.notices


@pytest.mark.parametrize("abort", [False, True])
def test_ST_091_002_IT_091_001_片方の未完や中断で他方を失わない(abort: bool) -> None:
    rows = overlapping()
    if abort:
        rows.append(event("turn_aborted", "t2"))
    rows += [final("返事1"), event("task_complete", "t1", last_agent_message="返事1")]
    result = CodexRecords().read(rows)
    assert [one.turn for one in result.conversations] == ["t1"]
    assert any(("中断" if abort else "未完") in notice for notice in result.notices)


def test_ST_091_002_IT_091_001_不一致の本文を結ばず完成断片を再利用しない() -> None:
    rows = [
        *overlapping(),
        final("返事"),
        event("task_complete", "t1", last_agent_message="別の返事"),
        event("task_complete", "t2", last_agent_message="返事"),
    ]
    result = CodexRecords().read(rows)
    assert not result.conversations


@pytest.mark.parametrize("image_only", [False, True])
@pytest.mark.parametrize(
    "supplement",
    [
        "[Image: original 1280x2400, displayed at 1067x2000. "
        + "Multiply coordinates by 1.20 to map to original image.]",
        "[Image: source: /tmp/fixture.png]",
    ],
)
def test_ST_091_002_IT_091_001_画像付加文は本文付き発話だけを復元する(
    tmp_path: Path, image_only: bool, supplement: str
) -> None:
    original = user("u", None, "")
    parts: list[dict[str, object]] = [
        {"type": "image", "source": {"type": "base64", "data": "fixture"}}
    ]
    if not image_only:
        parts.insert(0, {"type": "text", "text": "画像を説明して"})
    original["message"] = {"role": "user", "content": parts}
    meta = user("meta", "u", supplement)
    meta["isMeta"] = True
    rows = [original, meta, answer("a", "meta", "架空の画像です")]
    result = SessionLogs().read("claude", log(tmp_path / "a.jsonl", rows))
    assert len(result.conversations) == (0 if image_only else 1)
    if result.conversations:
        assert result.conversations[0].utterance == "画像を説明して"
    bad = copy.deepcopy(rows)
    bad[1]["message"] = {"role": "user", "content": supplement + "\n[Request interrupted]"}
    assert not SessionLogs().read("claude", log(tmp_path / "b.jsonl", bad)).conversations


@pytest.mark.parametrize(
    "boundary",
    [
        "valid",
        "wrong-id",
        "wrong-tool",
        "different-session",
        "interrupted",
        "other-user",
        "unknown-meta",
        "other-branch",
    ],
)
def test_ST_091_002_IT_091_001_Skill付加文は同じ親鎖の呼出しに限る(
    tmp_path: Path, boundary: str
) -> None:
    call = answer("call", "u", "")
    call["message"] = {
        "role": "assistant",
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "name": "Read" if boundary == "wrong-tool" else "Skill",
                "id": "skill-call",
            }
        ],
    }
    tool = user("result", "call", "")
    tool["message"] = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "skill-call", "content": "ok"}],
    }
    meta = user("meta", "result", "自由なスキル説明")
    meta.update(
        {"isMeta": True, "sourceToolUseID": "unknown" if boundary == "wrong-id" else "skill-call"}
    )
    rows = [user("u", None, "スキルを使って"), call, tool]
    if boundary in {"interrupted", "other-user", "unknown-meta"}:
        between = user(
            "between",
            "result",
            "[Request interrupted]" if boundary == "interrupted" else "割り込み",
        )
        if boundary == "unknown-meta":
            between["isMeta"] = True
        rows.append(between)
        meta["parentUuid"] = "between"
    if boundary == "different-session":
        call["sessionId"] = "other"
    if boundary == "other-branch":
        tool["parentUuid"] = "u"
    rows += [meta, answer("a", "meta", "スキルの応対")]
    result = SessionLogs().read("claude", log(tmp_path / "a.jsonl", rows))
    assert len(result.conversations) == (1 if boundary == "valid" else 0)


def test_ST_091_003_IT_091_002_複数発話は後のcontextでも保留し既存を変えない(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from yadori.adapter.embedding import CharacterPairs
    from yadori.adapter.importing.archive import SqliteArchive
    from yadori.domain.importing.model import ExternalConversation
    from yadori.usecase.importing.service import Importing

    rows: list[dict[str, object]] = [
        {"type": "session_meta", "payload": {"id": "fixture"}},
        event("task_started", "t1"),
        human("t1"),
        human("correction"),
        {"type": "turn_context", "payload": {"turn_id": "t1"}},
        final("返事1"),
        event("task_complete", "t1", last_agent_message="返事1"),
        event("task_started", "t2"),
        human("t2"),
        final("返事2"),
        event("task_complete", "t2", last_agent_message="返事2"),
    ]
    result = CodexRecords().read(rows)
    assert len(result.held) == 1
    assert result.conversations[0].previous == "t1"
    old = ExternalConversation(
        "codex", "fixture", "t1", "t1", datetime(2026, 9, 14, tzinfo=UTC), "質問correction", "返事1"
    )
    archive = SqliteArchive(tmp_path / "memory.sqlite")
    pairs = CharacterPairs()
    for record in [old, result.conversations[0]]:
        assert archive.keep("person", record, pairs.name, pairs.to_remember(record.utterance))
    before = [
        archive.existing("person", record.source) for record in [old, result.conversations[0]]
    ]
    importing = Importing(archive, "person")
    plan = importing.preview_logs([result])
    assert plan.held == 2 and plan.dependent == 1 and not plan.added and plan.existing == 0
    assert importing.apply(plan, pairs) == 0
    assert before == [
        archive.existing("person", record.source) for record in [old, result.conversations[0]]
    ]


def test_ST_091_002_IT_091_001_前の完成本文を次の発話へ流用しない() -> None:
    rows: list[dict[str, object]] = [
        {"type": "session_meta", "payload": {"id": "fixture"}},
        event("task_started", "t1"),
        human("t1"),
        final("same"),
        event("task_started", "t2"),
        human("t2"),
        event("task_complete", "t2", last_agent_message="same"),
    ]
    assert not CodexRecords().read(rows).conversations


@pytest.mark.parametrize("notice", ["duplicate", "abort-other", "abort-unknown"])
def test_ST_091_002_IT_091_001_他の完了通知や中断で完成本文を消さない(notice: str) -> None:
    rows: list[dict[str, object]] = [{"type": "session_meta", "payload": {"id": "fixture"}}]
    if notice == "duplicate":
        rows += [
            event("task_started", "t1"),
            human("t1"),
            final("one"),
            event("task_complete", "t1", last_agent_message="one"),
            event("task_started", "t2"),
            human("t2"),
            final("two"),
            event("task_complete", "t1", last_agent_message="one"),
            event("task_complete", "t2", last_agent_message="two"),
        ]
        assert len(CodexRecords().read(rows).conversations) == 2
    else:
        rows = overlapping()
        rows += [
            final("one"),
            event("turn_aborted", "t2" if notice == "abort-other" else "unknown"),
            event("task_complete", "t1", last_agent_message="one"),
        ]
        result = CodexRecords().read(rows)
        assert [one.turn for one in result.conversations] == ["t1"]
        if notice == "abort-unknown":
            assert any("未完" in n for n in result.notices)


@pytest.mark.parametrize(
    "boundary", ["valid", "wrong-id", "different-session", "mixed-text", "image-only-user"]
)
def test_ST_091_002_IT_091_001_Readの返却画像だけを付加文として辿る(
    tmp_path: Path, boundary: str
) -> None:
    original = user("u", None, "画像を読んで")
    image: dict[str, object] = {"type": "image", "source": {"type": "base64", "data": "fixture"}}
    if boundary == "image-only-user":
        original["message"] = {"role": "user", "content": [image]}
    call = answer("call", "u", "")
    call["message"] = {
        "role": "assistant",
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "read", "name": "Read"}],
    }
    result = user("result", "call", "")
    result["message"] = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "wrong" if boundary == "wrong-id" else "read",
                "content": "image",
            }
        ],
    }
    meta = user("meta", "result", "")
    parts: list[dict[str, object]] = [image]
    if boundary == "mixed-text":
        parts.append({"type": "text", "text": "未知の指示"})
    meta.update({"isMeta": True, "message": {"role": "user", "content": parts}})
    if boundary == "different-session":
        result["sessionId"] = "other"
    read = SessionLogs().read(
        "claude",
        log(
            tmp_path / "a.jsonl", [original, call, result, meta, answer("a", "meta", "画像の返事")]
        ),
    )
    assert len(read.conversations) == (1 if boundary == "valid" else 0)

"""管理行のゼロ埋めと、単一ログの複数完成を安全に区別する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.importing.archive import SqliteArchive
from yadori.adapter.importing.records import SessionLogs
from yadori.domain.importing.model import ImportFailed
from yadori.usecase.importing.service import Importing

AT = "2026-09-14T00:00:00+00:00"


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


def ambiguous() -> list[dict[str, object]]:
    tool = user("tool-result", "a1", "")
    tool["message"] = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call", "content": "処理結果"}],
    }
    return [
        user("u1", None, "最初の問い"),
        answer("a1", "u1", "応対X"),
        tool,
        answer("a2", "tool-result", "応対Y"),
        answer("a3", "a2", "応対Z"),
        user("u2", "a3", "別の問い"),
        answer("a4", "u2", "確定した応対"),
    ]


def test_ST_091_007_IT_091_001_既知管理行の先頭NULだけ読取時に無視する(tmp_path: Path) -> None:
    management: dict[str, object] = {
        "type": "last-prompt",
        "lastPrompt": "fixture",
        "leafUuid": "a",
        "sessionId": "fixture",
    }
    original = (
        encoded([user("u", None, "問い")])
        + b"\x00" * 3043
        + encoded([management, answer("a", "u", "返事")])
    )
    path = tmp_path / "session.jsonl"
    _ = path.write_bytes(original)
    result = SessionLogs().read("claude", path)
    assert len(result.conversations) == 1
    assert any("管理行 2" in notice and "3043 バイト" in notice for notice in result.notices)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "content",
    [
        b'\0{"type":"user"}\n',
        b'\0{"type":"last-prompt","lastPrompt":"fixture"}\n',
        b'\0{"type":"last-prompt","lastPrompt":"bad\0text","leafUuid":"a","sessionId":"fixture"}\n',
        b"{broken}\n",
    ],
)
def test_ST_091_007_本文内NULと未知の破損は引き続き停止する(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "session.jsonl"
    _ = path.write_bytes(content)
    with pytest.raises(ImportFailed):
        _ = SessionLogs().read("claude", path)
    assert path.read_bytes() == content


def test_ST_091_007_Claude以外へNULの例外を広げない(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _ = path.write_bytes(
        b"\0"
        + encoded(
            [
                {
                    "type": "last-prompt",
                    "lastPrompt": "fixture",
                    "leafUuid": "a",
                    "sessionId": "fixture",
                }
            ]
        )
    )
    with pytest.raises(ImportFailed):
        _ = SessionLogs().read("codex", path)


def test_ST_091_002_IT_091_001_複数完成は最初も保留し後の候補で復活させない(tmp_path: Path) -> None:
    result = SessionLogs().read("claude", log(tmp_path / "a.jsonl", ambiguous()))
    assert [one.turn for one in result.conversations] == ["u2"]
    assert result.conversations[0].previous == "u1"
    assert [one.answer for one in result.held] == ["a1", "a2", "a3"]
    assert any("1 発話を保留" in notice for notice in result.notices)


def test_ST_091_003_IT_091_002_既存を上書きせず他の確定した往復だけを追加する(
    tmp_path: Path,
) -> None:
    rows = [
        *ambiguous(),
        user("independent", None, "独立した問い"),
        answer("ind-a", "independent", "返事"),
    ]
    result = SessionLogs().read("claude", log(tmp_path / "a.jsonl", rows))
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    pairs = CharacterPairs()
    old = result.held[0]
    assert archive.keep("person", old, pairs.name, pairs.to_remember(old.utterance))
    before = archive.existing("person", old.source)
    importing = Importing(archive, "person")
    plan = importing.preview_logs([result])
    assert plan.held == 2 and plan.dependent == 1 and len(plan.added) == 1
    assert importing.apply(plan, pairs) == 1
    assert archive.existing("person", old.source) == before
    again = importing.preview_logs([result])
    assert again.held == 2 and not again.added and again.existing == 1


def test_ST_091_003_IT_091_002_保留を別ファイルで復活させず内容競合は全件停止する(
    tmp_path: Path,
) -> None:
    reader = SessionLogs()
    a = reader.read("claude", log(tmp_path / "a.jsonl", ambiguous()))
    b = reader.read("claude", log(tmp_path / "b.jsonl", ambiguous()[:2]))
    path = tmp_path / "memories.sqlite"
    importing = Importing(SqliteArchive(path), "person")
    with pytest.raises(ImportFailed, match="複数ファイル"):
        _ = importing.preview_logs([a, b])
    assert not path.exists()
    same = importing.preview_logs([a, a])
    assert same.held == 2 and not same.added


def test_ST_091_003_IT_091_002_既存の先行関係を変更せず保留を明示関係だけに伝播する(
    tmp_path: Path,
) -> None:
    reader = SessionLogs()
    rows = [
        user("u1", None, "問いA"),
        answer("a1", "u1", "返事A"),
        user("u2", "a1", "問いB"),
        answer("a2", "u2", "返事B"),
    ]
    path = log(tmp_path / "old.jsonl", rows)
    archive = SqliteArchive(tmp_path / "memories.sqlite")
    importing = Importing(archive, "person")
    original = reader.read("claude", path)
    assert original.conversations[1].previous == "u1"
    assert importing.apply(importing.preview_logs([original]), CharacterPairs()) == 2
    before = [archive.existing("person", one.source) for one in original.conversations]
    rows += [
        answer("alternative", "u1", "別の返事A"),
        user("u3", "a2", "問いC"),
        answer("a3", "u3", "返事C"),
        user("independent", None, "独立した問い"),
        answer("ind-a", "independent", "独立した返事"),
    ]
    changed = reader.read("claude", log(path, rows))
    plan = importing.preview_logs([changed])
    assert plan.held == 3 and plan.dependent == 2 and plan.existing == 0
    assert [one.turn for one in plan.added] == ["independent"]
    assert importing.apply(plan, CharacterPairs()) == 1
    assert [archive.existing("person", one.source) for one in original.conversations] == before
    again = importing.preview_logs([changed])
    assert again.held == 3 and again.existing == 1 and not again.added

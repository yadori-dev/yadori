"""記録の形の契約テスト。手元にある本物の記録に当てる。

Claude Code と Codex の記録の形はこちらの都合では変わらない。架空の記録で書いた
テストだけだと、本物の形が変わっても緑のままになる（`ST-030-005`）。件数だけを
見て、発話は出力しない。記録が手元に無い環境では飛ばす。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yadori.adapter.evaluation import ClaudeCodeRecords, CodexRecords
from yadori.domain.evaluation import BrokenRecord, Records

CLAUDE_CODE = Path.home() / ".claude" / "projects"
CODEX = Path.home() / ".codex" / "sessions"


# 本物の記録で観測した割合。返事を持つ一往復がこれを下回れば、返事の取り方が形と合っていない。
REPLIED_AT_LEAST = 0.8


def _recorded(reader: Records, place: Path) -> int:
    if not place.is_dir():
        pytest.skip(f"{place.name} の記録が手元に無い")
    count = 0
    with_workspace = 0
    replied = 0
    for path in sorted(place.rglob("*.jsonl"))[:200]:
        if not reader.claims(path):
            continue
        try:
            read = reader.read(path)
        except BrokenRecord:
            continue
        count += len(read)
        with_workspace += sum(1 for one in read if one.workspace and one.session)
        replied += sum(1 for one in read if one.reply)
        _refuse_tool_made(path, read)
    assert count >= 1, "一往復を一件も取り出せない。記録の形が変わった可能性がある"
    assert with_workspace == count, "作業場所かセッションを持たない一往復がある"
    assert replied / count >= REPLIED_AT_LEAST, f"返事を持つ一往復が {replied}/{count} しか無い"
    return count


def _refuse_tool_made(path: Path, read: tuple[object, ...]) -> None:
    """道具が自分で作った文章（子エージェントへの指示）が発話として混ざっていないか。"""
    import json

    sidechain: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row: object = json.loads(line)  # pyright: ignore[reportAny]
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("isSidechain"):  # pyright: ignore[reportUnknownMemberType]
            message: object = row.get("message")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(message, dict) and isinstance(message.get("content"), str):  # pyright: ignore[reportUnknownMemberType]
                sidechain.add(str(message["content"]))  # pyright: ignore[reportUnknownArgumentType]
    from yadori.domain.evaluation import Recorded

    for one in read:
        assert isinstance(one, Recorded)
        assert one.utterance not in sidechain, "子エージェントへの指示が発話として混ざっている"


@pytest.mark.contract
class TestRecordsContract:
    def test_ST_030_005_Claude_Codeの記録から一往復が取り出せ作業場所を持つ(self) -> None:
        print(f"claude code: {_recorded(ClaudeCodeRecords(), CLAUDE_CODE)} 件")

    def test_ST_030_005_Codexの記録から一往復が取り出せ作業場所を持つ(self) -> None:
        print(f"codex: {_recorded(CodexRecords(), CODEX)} 件")

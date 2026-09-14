"""agyの観測済み通知を、共通の予算と限定した許可へ結ぶ。"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_078_agy import Probe
from yadori.adapter.recall.ledger import RecallLedger
from yadori.adapter.tool.agy_notice import AgyJson
from yadori.domain.recall.model import RecallFailure
from yadori.infrastructure.agy import AgyHook


def token(context: str) -> str:
    matched = re.search(r"今回のturnは ([a-f0-9]{32})", context)
    assert matched is not None
    return matched.group(1)


def test_ST_082_003_IT_082_001_agyの遅れたStopは進行中の新しい往復を閉じない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    code, first = probe.call("pre", monkeypatch)
    assert code == 0
    ledger = RecallLedger(probe.run, "sora")
    _ = ledger.reserve(token(first), "search", "query")
    assert probe.call("pre", monkeypatch) == (code, first)
    probe.write(probe.rows)
    assert probe.call("stop", monkeypatch)[0] == 0
    with pytest.raises(RecallFailure, match="往復キー"):
        _ = ledger.reserve(token(first), "search", "query")
    rows = [dict(row, step_index=AgyJson.number(row, "step_index") + 10) for row in probe.rows]
    probe.pre["initialNumSteps"] = 11
    probe.write([*probe.rows, rows[0]])
    code, second = probe.call("pre", monkeypatch)
    assert code == 0 and token(second) != token(first)
    assert probe.call("stop", monkeypatch)[0] == 0
    _ = ledger.reserve(token(second), "search", "query")
    probe.write([*probe.rows, *rows])
    assert probe.call("stop", monkeypatch)[0] == 0
    assert probe.call("pre", monkeypatch) == (code, second)
    with pytest.raises(RecallFailure):
        _ = ledger.reserve(token(second), "search", "query")
    assert probe.count() == 2


@pytest.mark.parametrize(
    "server,tool,decision",
    [
        ("yadori_memory", "recall_search", "allow"),
        ("yadori_memory", "recall_get", "allow"),
        ("other", "recall_search", "deny"),
        ("yadori_memory", "erase", "deny"),
    ],
)
def test_ST_082_007_IT_082_004_agyは観測した接続の読み取り2道具だけ許可する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server: str,
    tool: str,
    decision: str,
) -> None:
    probe = Probe(tmp_path)
    probe.write(probe.rows[:1])
    assert probe.call("pre", monkeypatch)[0] == 0
    fixture = AgyJson.object((Path(__file__).parent / "fixtures/agy-1.2.2-mcp.json").read_text())
    payload = AgyJson.object(json.dumps(fixture["mcp_tool"]))
    payload["conversationId"] = probe.session
    call = AgyJson.object(json.dumps(payload["toolCall"]))
    arguments = AgyJson.object(json.dumps(call["args"]))
    arguments.update(ServerName=server, ToolName=tool)
    call["args"] = arguments
    payload["toolCall"] = call
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", output)
    assert AgyHook("tool", probe.run, probe.home, probe.startup).run() == 0
    assert AgyJson.object(output.getvalue())["decision"] == decision
    assert probe.count() == 0


def test_ST_082_007_agyは拒否規則を守り二つの許可だけを追加する() -> None:
    from yadori.adapter.recall.connection import MemoryConnection, MemoryConnectionError

    with pytest.raises(MemoryConnectionError, match="拒否"):
        _ = MemoryConnection.agy_permissions([{"permissions": {"deny": ["mcp(yadori_memory/*)"]}}])
    assert MemoryConnection.agy_permissions([{"permissions": {"allow": ["mcp(*)"]}}]) == {
        "allow": ["mcp(yadori_memory/recall_search)", "mcp(yadori_memory/recall_get)"]
    }

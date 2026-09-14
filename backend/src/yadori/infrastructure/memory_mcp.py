"""起動時の人物に固定した記憶のMCP。標準出力は接続のためだけに使う。"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import final

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from yadori.adapter.embedding import DefaultEmbeddings
from yadori.adapter.importing.archive import SqliteArchive
from yadori.adapter.recall.ledger import RecallLedger, TurnReferences
from yadori.adapter.store import SqliteMemories
from yadori.domain.memory import EmbeddingsUnavailable
from yadori.domain.recall.model import Candidates, Page, Problem, RecallFailure
from yadori.infrastructure.settings import NotSettled, SettingsFile
from yadori.usecase.recall.service import Reading


@final
class MemoryTools:
    def __init__(self, ledger: RecallLedger, reading: Reading) -> None:
        self._ledger = ledger
        self._reading = reading
        self._lock = Lock()

    def search(self, turn: str, query: str, limit: int = 3) -> str:
        return self._call(
            turn,
            "search",
            json.dumps({"query": query, "limit": limit}, ensure_ascii=False),
            lambda references: self._reading.search(references, query, limit),
        )

    def get(self, turn: str, reference: str, offset: int = 0) -> str:
        return self._call(
            turn,
            "get",
            json.dumps({"reference": reference, "offset": offset}),
            lambda references: self._reading.get(references, reference, offset),
        )

    def _call(
        self,
        turn: str,
        operation: str,
        request: str,
        run: Callable[[TurnReferences], Candidates | Page],
    ) -> str:
        try:
            ticket = self._ledger.reserve(turn, operation, request)
            try:
                with self._lock:
                    result: Candidates | Page | Problem = run(self._ledger.references(ticket))
            except RecallFailure as trouble:
                result = Problem(trouble.code, str(trouble))
            except (EmbeddingsUnavailable, sqlite3.Error, OSError):
                result = Problem(
                    "unavailable",
                    "記憶の保存先または埋め込みを使えません。今回は断定せず不足点を確認してください",
                )
            return self._ledger.publish(ticket, result)
        except RecallFailure as trouble:
            return json.dumps(asdict(Problem(trouble.code, str(trouble))), ensure_ascii=False)
        except (sqlite3.Error, OSError):
            return json.dumps(
                asdict(
                    Problem("unavailable", "記憶検索の台帳を確認できません。起動し直してください")
                ),
                ensure_ascii=False,
            )

    def server(self) -> MCPServer[None]:
        server: MCPServer[None] = MCPServer("yadori_memory")
        annotations = ToolAnnotations(
            read_only_hint=True, destructive_hint=False, open_world_hint=False
        )
        server.add_tool(
            self.search,
            name="recall_search",
            structured_output=False,
            annotations=annotations,
            description="過去の事実の根拠が不足するとき検索語を変えて記憶の短い候補を探す。最大3件。今回のturnが必須。十分な原文があれば不要。候補だけで断定せずrecall_getで原文を読む。",
        )
        server.add_tool(
            self.get,
            name="recall_get",
            structured_output=False,
            annotations=annotations,
            description="候補のreferenceから原文と限られた前後を読む。今回のturnが必須。complete=falseならnext_offsetで続き、未取得を確認済みにしない。limit_reachedなら止めて不足点を尋ねる。",
        )
        return server


class MemoryMCP:
    @staticmethod
    def run(run_dir: Path) -> int:
        memories: SqliteMemories | None = None
        try:
            settings = SettingsFile().read()
            if not settings.memories_path.is_file():
                raise NotSettled("記憶の保存先がありません。宿りとして起動してください")
            ledger = RecallLedger(run_dir, settings.dweller.id)
            memories = SqliteMemories(settings.memories_path)
            if memories.dweller(settings.dweller.id) is None:
                raise NotSettled("起動した人物の記憶がありません")
            reading = Reading(
                memories,
                DefaultEmbeddings()(settings.models_path),
                settings.dweller.id,
                SqliteArchive(settings.memories_path),
            )
            MemoryTools(ledger, reading).server().run(transport="stdio")
            return 0
        except (NotSettled, RecallFailure, sqlite3.Error, OSError) as trouble:
            print(f"記憶の接続を起動できません: {trouble}", file=sys.stderr)
            return 1
        finally:
            if memories is not None:
                memories.close()

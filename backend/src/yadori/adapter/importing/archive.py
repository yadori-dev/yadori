"""外部会話だけを別表へ保存する。確認と検索は読み取り専用で開く。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from yadori.adapter.embedding.characters import Closeness
from yadori.adapter.store.sqlite import Row
from yadori.domain.importing.model import (
    ExternalConversation,
    ExternalFound,
    Imported,
    ImportFailed,
    Provider,
)
from yadori.domain.memory.model import Vector

_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_conversation (
 id INTEGER PRIMARY KEY, person TEXT NOT NULL, source TEXT NOT NULL,
 provider TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, answer TEXT NOT NULL,
 happened_at TEXT NOT NULL, utterance TEXT NOT NULL, reply TEXT NOT NULL,
 previous TEXT, imported_at TEXT NOT NULL, UNIQUE(person,source)
);
CREATE TABLE IF NOT EXISTS external_index (
 record INTEGER NOT NULL REFERENCES external_conversation(id), model TEXT NOT NULL,
 vector TEXT NOT NULL, PRIMARY KEY(record,model)
);
"""


@final
class SqliteArchive:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._closeness = Closeness()

    def existing(self, person: str, source: str) -> Imported | None:
        rows = self._read(
            "SELECT * FROM external_conversation WHERE person=? AND source=?", (person, source)
        )
        return self._record(rows[0]) if rows else None

    def native_exists(self, person: str, source: str) -> bool:
        with self._opened(False) as connection:
            if connection is None or not self._table(connection, "episode"):
                return False
            raw_columns: list[sqlite3.Row] = connection.execute(
                "PRAGMA table_info(episode)"
            ).fetchall()
            columns = [Row(row).text("name") for row in raw_columns]
            if "source" not in columns:
                return False
            return (
                connection.execute(
                    "SELECT 1 FROM episode WHERE dweller_id=? AND source=?", (person, source)
                ).fetchone()
                is not None
            )

    def get(self, person: str, identifier: int) -> Imported | None:
        rows = self._read(
            "SELECT * FROM external_conversation WHERE person=? AND id=?", (person, identifier)
        )
        return self._record(rows[0]) if rows else None

    def following(self, person: str, conversation: ExternalConversation) -> tuple[Imported, ...]:
        return tuple(
            self._record(row)
            for row in self._read(
                "SELECT * FROM external_conversation WHERE person=? AND provider=? AND"
                + " session=? AND previous=? ORDER BY id LIMIT 2",
                (person, conversation.provider, conversation.session, conversation.turn),
            )
        )

    def keep(
        self, person: str, conversation: ExternalConversation, model: str, vector: Vector
    ) -> bool:
        with self._opened(True) as connection:
            assert connection is not None
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "SELECT * FROM external_conversation WHERE person=? AND source=?",
                (person, conversation.source),
            )
            raw: sqlite3.Row | None = cursor.fetchone()  # pyright: ignore[reportAny]
            if raw is not None:
                existing = self._record(Row(raw))
                if existing.conversation != conversation:
                    raise ImportFailed(f"同じ出典の原文が変わっています: {conversation.source}")
                return False
            cursor = connection.execute(
                "INSERT INTO external_conversation(person,source,provider,session,turn,answer,"
                + "happened_at,utterance,reply,previous,imported_at) "
                + "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    person,
                    conversation.source,
                    conversation.provider,
                    conversation.session,
                    conversation.turn,
                    conversation.answer,
                    conversation.happened_at.isoformat(),
                    conversation.utterance,
                    conversation.reply,
                    conversation.previous,
                    datetime.now(UTC).isoformat(),
                ),
            )
            _ = connection.execute(
                "INSERT INTO external_index VALUES (?,?,?)",
                (cursor.lastrowid, model, json.dumps(vector)),
            )
            return True

    def search(
        self, person: str, model: str, vector: Vector, floor: float, limit: int
    ) -> tuple[ExternalFound, ...]:
        rows = self._read(
            "SELECT e.*,i.vector FROM external_conversation e JOIN external_index i ON"
            + " i.record=e.id WHERE e.person=? AND i.model=?",
            (person, model),
        )
        found = [
            ExternalFound(
                self._record(row), self._closeness.between(vector, self._vector(row)), model
            )
            for row in rows
        ]
        return tuple(
            sorted(
                (one for one in found if one.relevance >= floor),
                key=lambda one: (-one.relevance, -one.record.id),
            )[:limit]
        )

    def unindexed(self, person: str, model: str) -> tuple[Imported, ...]:
        return tuple(
            self._record(row)
            for row in self._read(
                "SELECT e.* FROM external_conversation e WHERE e.person=? AND NOT EXISTS"
                + " (SELECT 1 FROM external_index i WHERE i.record=e.id AND i.model=?)"
                + " ORDER BY e.id",
                (person, model),
            )
        )

    def index(self, person: str, identifier: int, model: str, vector: Vector) -> None:
        with self._opened(True) as connection:
            assert connection is not None
            _ = connection.execute(
                "INSERT OR REPLACE INTO external_index SELECT id,?,? FROM"
                + " external_conversation WHERE id=? AND person=?",
                (model, json.dumps(vector), identifier, person),
            )

    def _read(self, sql: str, args: tuple[object, ...]) -> tuple[Row, ...]:
        with self._opened(False) as connection:
            if connection is None or not self._table(connection, "external_conversation"):
                return ()
            rows: list[sqlite3.Row] = connection.execute(sql, args).fetchall()
            return tuple(Row(row) for row in rows)

    def _table(self, connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    @contextmanager
    def _opened(self, write: bool) -> Generator[sqlite3.Connection | None]:
        if not write and not self._path.exists():
            yield None
            return
        connection = sqlite3.connect(
            self._path if write else self._path.resolve().as_uri() + "?mode=ro",
            uri=not write,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        try:
            if write:
                _ = connection.executescript(_SCHEMA)
            with connection:
                yield connection
        finally:
            connection.close()

    def _record(self, row: Row) -> Imported:
        provider = self._provider(row.text("provider"))
        return Imported(
            row.number("id"),
            ExternalConversation(
                provider,
                row.text("session"),
                row.text("turn"),
                row.text("answer"),
                datetime.fromisoformat(row.text("happened_at")),
                row.text("utterance"),
                row.text("reply"),
                row.text_or_none("previous"),
            ),
        )

    def _provider(self, name: str) -> Provider:
        if name not in {"claude", "codex", "agy"}:
            raise ImportFailed("外部会話の道具名が不正です")
        if name == "claude":
            return "claude"
        return "codex" if name == "codex" else "agy"

    def _vector(self, row: Row) -> Vector:
        values: object = json.loads(row.text("vector"))  # pyright: ignore[reportAny]
        if not isinstance(values, list):
            raise ImportFailed("外部会話の索引が不正です")
        return tuple(self._number(value) for value in values)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    def _number(self, value: object) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ImportFailed("外部会話の索引が数値ではありません")
        return float(value)

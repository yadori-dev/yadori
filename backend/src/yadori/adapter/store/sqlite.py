"""手元のファイル一つへ記憶を持つ。

原文と索引を別の表に置く。索引は原文から作り直せるため、消しても記憶は
失われない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.adapter.embedding.characters import Closeness
from yadori.domain.memory import Dweller, Episode, Identity, Retrieval, Vector

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dweller (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    nickname TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity (
    dweller_id TEXT NOT NULL REFERENCES dweller(id),
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (dweller_id, version)
);
CREATE TABLE IF NOT EXISTS episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dweller_id TEXT NOT NULL REFERENCES dweller(id),
    utterance TEXT NOT NULL,
    reply TEXT NOT NULL,
    identity_version INTEGER NOT NULL,
    happened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_index (
    episode_id INTEGER PRIMARY KEY REFERENCES episode(id),
    model TEXT NOT NULL,
    vector TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval (
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    at TEXT NOT NULL
);
"""


@final
class Row:
    """保存先から返る一行。

    保存先は型を約束しないため、取り出すときに確かめる。表の形が変わったら
    黙って別の値を使うのではなく、そこで失敗させる。
    """

    def __init__(self, row: sqlite3.Row) -> None:
        self._row: sqlite3.Row = row

    def text(self, column: str) -> str:
        # 標準ライブラリは行の値へ型を付けないため、ここで受けて確かめる。
        value: object = self._row[column]  # pyright: ignore[reportAny]
        if not isinstance(value, str):
            raise TypeError(f"{column} は文字ではない: {value!r}")
        return value

    def number(self, column: str) -> int:
        value: object = self._row[column]  # pyright: ignore[reportAny]
        if not isinstance(value, int):
            raise TypeError(f"{column} は数ではない: {value!r}")
        return value

    def text_or_none(self, column: str) -> str | None:
        value: object = self._row[column]  # pyright: ignore[reportAny]
        if value is None:
            return None
        return self.text(column)


@final
class SqliteMemories:
    def __init__(self, path: Path | str) -> None:
        self._closeness: Closeness = Closeness()
        self._connection: sqlite3.Connection = sqlite3.connect(path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        _ = self._connection.execute("PRAGMA foreign_keys = ON")
        _ = self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def settle(self, dweller: Dweller) -> None:
        """宿りを住まわせる。名前と呼び名は宿り自身が持つ。"""
        self._run(
            "INSERT OR REPLACE INTO dweller (id, owner, name, nickname) VALUES (?, ?, ?, ?)",
            (dweller.id, dweller.owner, dweller.name, dweller.nickname),
        )

    def dweller(self, dweller_id: str) -> Dweller | None:
        row = self._one("SELECT * FROM dweller WHERE id = ?", (dweller_id,))
        if row is None:
            return None
        return Dweller(
            id=row.text("id"),
            owner=row.text("owner"),
            name=row.text("name"),
            nickname=row.text("nickname"),
        )

    def current_identity(self, dweller_id: str) -> Identity | None:
        row = self._one(
            "SELECT version, text FROM identity WHERE dweller_id = ?"
            + " ORDER BY version DESC LIMIT 1",
            (dweller_id,),
        )
        return None if row is None else self._as_identity(row)

    def write_identity(self, dweller_id: str, text: str) -> Identity:
        current = self.current_identity(dweller_id)
        version = 1 if current is None else current.version + 1
        self._run(
            "INSERT INTO identity (dweller_id, version, text) VALUES (?, ?, ?)",
            (dweller_id, version, text),
        )
        return Identity(version=version, text=text)

    def identity_at(self, dweller_id: str, version: int) -> Identity | None:
        row = self._one(
            "SELECT version, text FROM identity WHERE dweller_id = ? AND version = ?",
            (dweller_id, version),
        )
        return None if row is None else self._as_identity(row)

    def recent(self, dweller_id: str, limit: int) -> tuple[Episode, ...]:
        rows = self._all(
            "SELECT * FROM episode WHERE dweller_id = ? ORDER BY id DESC LIMIT ?",
            (dweller_id, limit),
        )
        return tuple(self._as_episode(row) for row in reversed(rows))

    def search(
        self,
        dweller_id: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
    ) -> tuple[tuple[Episode, float], ...]:
        rows = self._all(
            "SELECT e.*, i.vector AS vector FROM episode e"
            + " JOIN episode_index i ON i.episode_id = e.id"
            + " WHERE e.dweller_id = ?",
            (dweller_id,),
        )
        excluded = set(exclude)
        scored = [
            (self._as_episode(row), self._closeness.between(vector, self._as_vector(row)))
            for row in rows
            if row.number("id") not in excluded
        ]
        near = [pair for pair in scored if pair[1] >= floor]
        near.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return tuple(near[:limit])

    def write_episode(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        identity_version: int,
        happened_at: datetime,
    ) -> Episode:
        cursor = self._connection.execute(
            "INSERT INTO episode (dweller_id, utterance, reply, identity_version, happened_at)"
            + " VALUES (?, ?, ?, ?, ?)",
            (dweller_id, utterance, reply, identity_version, happened_at.isoformat()),
        )
        return Episode(
            id=int(cursor.lastrowid or 0),
            utterance=utterance,
            reply=reply,
            identity_version=identity_version,
            happened_at=happened_at,
        )

    def count_episodes(self, dweller_id: str) -> int:
        row = self._one("SELECT COUNT(*) AS total FROM episode WHERE dweller_id = ?", (dweller_id,))
        return 0 if row is None else row.number("total")

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None:
        self._run(
            "INSERT OR REPLACE INTO episode_index (episode_id, model, vector) VALUES (?, ?, ?)",
            (episode_id, model, self._as_text(vector)),
        )

    def clear_index(self, dweller_id: str) -> None:
        self._run(
            "DELETE FROM episode_index WHERE episode_id IN"
            + " (SELECT id FROM episode WHERE dweller_id = ?)",
            (dweller_id,),
        )

    def episodes_without_index(self, dweller_id: str) -> tuple[Episode, ...]:
        rows = self._all(
            "SELECT e.* FROM episode e"
            + " LEFT JOIN episode_index i ON i.episode_id = e.id"
            + " WHERE e.dweller_id = ? AND i.episode_id IS NULL ORDER BY e.id",
            (dweller_id,),
        )
        return tuple(self._as_episode(row) for row in rows)

    def record_retrieval(self, episode_ids: Collection[int], at: datetime) -> None:
        _ = self._connection.executemany(
            "INSERT INTO retrieval (episode_id, at) VALUES (?, ?)",
            [(episode_id, at.isoformat()) for episode_id in episode_ids],
        )

    def retrieval(self, episode_id: int) -> Retrieval:
        row = self._one(
            "SELECT COUNT(*) AS total, MAX(at) AS last FROM retrieval WHERE episode_id = ?",
            (episode_id,),
        )
        if row is None:
            return Retrieval(count=0, last_at=None)
        last = row.text_or_none("last")
        return Retrieval(
            count=row.number("total"),
            last_at=None if last is None else datetime.fromisoformat(last),
        )

    def _run(self, sql: str, params: tuple[object, ...]) -> None:
        _ = self._connection.execute(sql, params)

    def _one(self, sql: str, params: tuple[object, ...]) -> Row | None:
        found: sqlite3.Row | None = self._connection.execute(sql, params).fetchone()  # pyright: ignore[reportAny]
        return None if found is None else Row(found)

    def _all(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        rows: list[sqlite3.Row] = self._connection.execute(sql, params).fetchall()
        return [Row(found) for found in rows]

    def _as_identity(self, row: Row) -> Identity:
        return Identity(version=row.number("version"), text=row.text("text"))

    def _as_episode(self, row: Row) -> Episode:
        return Episode(
            id=row.number("id"),
            utterance=row.text("utterance"),
            reply=row.text("reply"),
            identity_version=row.number("identity_version"),
            happened_at=datetime.fromisoformat(row.text("happened_at")),
        )

    def _as_text(self, vector: Vector) -> str:
        """索引を文字として持つ。JSON を通すと境界で型が消える。"""
        return ",".join(repr(value) for value in vector)

    def _as_vector(self, row: Row) -> Vector:
        return tuple(float(part) for part in row.text("vector").split(","))

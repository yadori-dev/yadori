"""手元のファイル一つへ記憶を持つ。

原文と索引を別の表に置く。索引は原文から作り直せるため、消しても記憶は
失われない。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection
from datetime import datetime
from pathlib import Path

from yadori.adapter.embedding.characters import closeness
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


def _episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=row["id"],
        utterance=row["utterance"],
        reply=row["reply"],
        identity_version=row["identity_version"],
        happened_at=datetime.fromisoformat(row["happened_at"]),
    )


class SqliteMemories:
    def __init__(self, path: Path | str) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def settle(self, dweller: Dweller) -> None:
        """宿りを住まわせる。名前と呼び名は宿り自身が持つ。"""
        self._connection.execute(
            "INSERT OR REPLACE INTO dweller (id, owner, name, nickname) VALUES (?, ?, ?, ?)",
            (dweller.id, dweller.owner, dweller.name, dweller.nickname),
        )

    def dweller(self, dweller_id: str) -> Dweller | None:
        row = self._connection.execute(
            "SELECT * FROM dweller WHERE id = ?", (dweller_id,)
        ).fetchone()
        if row is None:
            return None
        return Dweller(id=row["id"], owner=row["owner"], name=row["name"], nickname=row["nickname"])

    def current_identity(self, dweller_id: str) -> Identity | None:
        row = self._connection.execute(
            "SELECT version, text FROM identity WHERE dweller_id = ? ORDER BY version DESC LIMIT 1",
            (dweller_id,),
        ).fetchone()
        if row is None:
            return None
        return Identity(version=row["version"], text=row["text"])

    def write_identity(self, dweller_id: str, text: str) -> Identity:
        current = self.current_identity(dweller_id)
        version = 1 if current is None else current.version + 1
        self._connection.execute(
            "INSERT INTO identity (dweller_id, version, text) VALUES (?, ?, ?)",
            (dweller_id, version, text),
        )
        return Identity(version=version, text=text)

    def identity_at(self, dweller_id: str, version: int) -> Identity | None:
        row = self._connection.execute(
            "SELECT version, text FROM identity WHERE dweller_id = ? AND version = ?",
            (dweller_id, version),
        ).fetchone()
        if row is None:
            return None
        return Identity(version=row["version"], text=row["text"])

    def recent(self, dweller_id: str, limit: int) -> tuple[Episode, ...]:
        rows = self._connection.execute(
            "SELECT * FROM episode WHERE dweller_id = ? ORDER BY id DESC LIMIT ?",
            (dweller_id, limit),
        ).fetchall()
        return tuple(_episode(row) for row in reversed(rows))

    def search(
        self,
        dweller_id: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
    ) -> tuple[tuple[Episode, float], ...]:
        rows = self._connection.execute(
            "SELECT e.*, i.vector AS vector FROM episode e"
            " JOIN episode_index i ON i.episode_id = e.id"
            " WHERE e.dweller_id = ?",
            (dweller_id,),
        ).fetchall()
        excluded = set(exclude)
        scored = [
            (_episode(row), closeness(vector, tuple(json.loads(row["vector"]))))
            for row in rows
            if row["id"] not in excluded
        ]
        scored = [pair for pair in scored if pair[1] >= floor]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return tuple(scored[:limit])

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
            " VALUES (?, ?, ?, ?, ?)",
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
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM episode WHERE dweller_id = ?", (dweller_id,)
        ).fetchone()
        return int(row["total"])

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO episode_index (episode_id, model, vector) VALUES (?, ?, ?)",
            (episode_id, model, json.dumps(list(vector))),
        )

    def clear_index(self, dweller_id: str) -> None:
        self._connection.execute(
            "DELETE FROM episode_index WHERE episode_id IN"
            " (SELECT id FROM episode WHERE dweller_id = ?)",
            (dweller_id,),
        )

    def episodes_without_index(self, dweller_id: str) -> tuple[Episode, ...]:
        rows = self._connection.execute(
            "SELECT e.* FROM episode e"
            " LEFT JOIN episode_index i ON i.episode_id = e.id"
            " WHERE e.dweller_id = ? AND i.episode_id IS NULL ORDER BY e.id",
            (dweller_id,),
        ).fetchall()
        return tuple(_episode(row) for row in rows)

    def record_retrieval(self, episode_ids: Collection[int], at: datetime) -> None:
        self._connection.executemany(
            "INSERT INTO retrieval (episode_id, at) VALUES (?, ?)",
            [(episode_id, at.isoformat()) for episode_id in episode_ids],
        )

    def retrieval(self, episode_id: int) -> Retrieval:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total, MAX(at) AS last FROM retrieval WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        last = row["last"]
        return Retrieval(
            count=int(row["total"]),
            last_at=datetime.fromisoformat(last) if last else None,
        )

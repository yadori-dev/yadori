"""手元のファイル一つへ記憶を持つ。

原文とインデックスを別の表に置く。インデックスは原文から作り直せるため、消しても記憶は
失われない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import final

from yadori.adapter.embedding.characters import Closeness
from yadori.domain.memory import (
    Dream,
    Dweller,
    Episode,
    Gist,
    Identity,
    Moved,
    RememberingConflict,
    Retrieval,
    Shift,
    Vector,
)

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
    happened_at TEXT NOT NULL,
    recalled_at TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS episode_index (
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    model TEXT NOT NULL,
    vector TEXT NOT NULL,
    PRIMARY KEY (episode_id, model)
);
CREATE TABLE IF NOT EXISTS retrieval (
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dweller_id TEXT NOT NULL REFERENCES dweller(id),
    at TEXT NOT NULL,
    delta REAL NOT NULL,
    cause TEXT NOT NULL,
    episode_id INTEGER REFERENCES episode(id)
);
CREATE TABLE IF NOT EXISTS dream (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dweller_id TEXT NOT NULL REFERENCES dweller(id),
    at TEXT NOT NULL,
    read_from TEXT NOT NULL,
    read_to TEXT NOT NULL,
    count INTEGER NOT NULL,
    kept INTEGER NOT NULL,
    noticing TEXT
);
CREATE TABLE IF NOT EXISTS gist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dweller_id TEXT NOT NULL REFERENCES dweller(id),
    dream_id INTEGER NOT NULL REFERENCES dream(id),
    made_at TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gist_source (
    gist_id INTEGER NOT NULL REFERENCES gist(id),
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    PRIMARY KEY (gist_id, episode_id)
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

    def real(self, column: str) -> float:
        value: object = self._row[column]  # pyright: ignore[reportAny]
        if not isinstance(value, int | float):
            raise TypeError(f"{column} が数値でない: {value!r}")
        return float(value)

    def number_or_none(self, column: str) -> int | None:
        value: object = self._row[column]  # pyright: ignore[reportAny]
        if value is None:
            return None
        if not isinstance(value, int):
            raise TypeError(f"{column} が整数でない: {value!r}")
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
        # 話す場所によっては、応対を作る間だけ別のスレッドへ移す（Discord は待っている間も
        # 動き続ける必要がある）。同時に触らないことは場所が保つ（一往復ずつ順に扱う）ため、
        # スレッドをまたいで使えるようにする。
        self._connection: sqlite3.Connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        _ = self._connection.execute("PRAGMA foreign_keys = ON")
        self._prepare_schema()

    def _prepare_schema(self) -> None:
        """新旧どちらの保存先も、形を途中まで変えずに使用可能な形へする。"""
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._discard_old_index_table()
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    _ = self._connection.execute(statement)
            self._upgrade_exact_sources()
        except BaseException:
            _ = self._connection.execute("ROLLBACK")
            raise
        _ = self._connection.execute("COMMIT")

    def _upgrade_exact_sources(self) -> None:
        """以前の原文を変えず、出典と思い出した時刻と一往復一動きの制約を足す。"""
        columns = {column.text("name") for column in self._all("PRAGMA table_info(episode)", ())}
        if "recalled_at" not in columns:
            _ = self._connection.execute("ALTER TABLE episode ADD COLUMN recalled_at TEXT")
        if "source" not in columns:
            _ = self._connection.execute("ALTER TABLE episode ADD COLUMN source TEXT")
        _ = self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS episode_source"
            + " ON episode (dweller_id, source) WHERE source IS NOT NULL"
        )
        _ = self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS shift_episode"
            + " ON shift (dweller_id, episode_id) WHERE episode_id IS NOT NULL"
        )

    def _discard_old_index_table(self) -> None:
        """以前の版が作ったインデックスの表は、形が違えば捨てる。原文の表には触れない。

        インデックスは原文から作り直せる派生物である（ADR-006）。以前の形は記憶ごとに
        一つのインデックスしか持てず、そのまま使うと埋め込みごとのインデックスが同じ場所へ黙って
        上書きされる。捨てれば、起動時にいまの埋め込みで作り直される。
        """
        columns = self._all("PRAGMA table_info(episode_index)", ())
        if not columns:
            return
        keyed = {column.text("name") for column in columns if column.number("pk") > 0}
        if keyed != {"episode_id", "model"}:
            _ = self._connection.execute("DROP TABLE episode_index")

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
        model: str,
        vector: Vector,
        limit: int,
        floor: float,
        exclude: Collection[int],
    ) -> tuple[tuple[Episode, float], ...]:
        # 違う埋め込みで作ったインデックスは使わない。長さが違えば比べようとして落ち、
        # 長さが同じなら誤った近さを黙って出す。
        rows = self._all(
            "SELECT e.*, i.vector AS vector FROM episode e"
            + " JOIN episode_index i ON i.episode_id = e.id"
            + " WHERE e.dweller_id = ? AND i.model = ?",
            (dweller_id, model),
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
        recalled_at: datetime | None = None,
        source: str | None = None,
    ) -> Episode:
        cursor = self._connection.execute(
            "INSERT INTO episode"
            + " (dweller_id, utterance, reply, identity_version, happened_at, recalled_at, source)"
            + " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING RETURNING *",
            (
                dweller_id,
                utterance,
                reply,
                identity_version,
                happened_at.isoformat(),
                None if recalled_at is None else recalled_at.isoformat(),
                source,
            ),
        )
        inserted: sqlite3.Row | None = cursor.fetchone()  # pyright: ignore[reportAny]
        if inserted is not None:
            return self._as_episode(Row(inserted))
        if source is None:
            raise RuntimeError("出典の無い一往復を書けなかった")
        row = self._one(
            "SELECT * FROM episode WHERE dweller_id = ? AND source = ?", (dweller_id, source)
        )
        if row is None:
            raise RuntimeError(f"出典 {source} の一往復を書けなかった")
        kept = self._as_episode(row)
        if (
            kept.utterance != utterance
            or kept.reply != reply
            or kept.identity_version != identity_version
            or kept.recalled_at != recalled_at
        ):
            raise RememberingConflict(f"同じ出典へ異なる一往復が届いた: {source}")
        return kept

    def episode(self, episode_id: int) -> Episode | None:
        row = self._one(
            "SELECT * FROM episode WHERE id = ?",
            (episode_id,),
        )
        return None if row is None else self._as_episode(row)

    def keep_episode(
        self,
        dweller_id: str,
        utterance: str,
        reply: str,
        identity_version: int,
        happened_at: datetime,
        recalled_at: datetime | None,
        source: str | None,
        indexes: Collection[tuple[str, Vector]],
        moved: Moved | None,
    ) -> Episode:
        """一往復と気持ちを同じ取引で確定し、索引は後から補える。"""
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._episode_from_source(dweller_id, source)
            episode = self.write_episode(
                dweller_id,
                utterance,
                reply,
                identity_version,
                happened_at,
                recalled_at,
                source,
            )
            if existing is not None:
                self._require_same_moved(dweller_id, episode.id, moved)
            elif moved is not None:
                self.record_shift(
                    dweller_id,
                    Shift(happened_at, moved.delta, moved.cause, episode.id),
                )
        except BaseException:
            _ = self._connection.execute("ROLLBACK")
            raise
        _ = self._connection.execute("COMMIT")
        for model, vector in indexes:
            self.write_index(episode.id, model, vector)
        return episode

    def _episode_from_source(self, dweller_id: str, source: str | None) -> Episode | None:
        if source is None:
            return None
        row = self._one(
            "SELECT * FROM episode WHERE dweller_id = ? AND source = ?", (dweller_id, source)
        )
        return None if row is None else self._as_episode(row)

    def _require_same_moved(self, dweller_id: str, episode_id: int, moved: Moved | None) -> None:
        row = self._one(
            "SELECT delta, cause FROM shift WHERE dweller_id = ? AND episode_id = ?",
            (dweller_id, episode_id),
        )
        if row is None and moved is None:
            return
        if (
            row is None
            or moved is None
            or row.real("delta") != moved.delta
            or row.text("cause") != moved.cause
        ):
            raise RememberingConflict(f"同じ一往復へ異なる気持ちの動きが届いた: {episode_id}")

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

    def episodes_without_index(self, dweller_id: str, model: str) -> tuple[Episode, ...]:
        """いまの埋め込みのインデックスを持たない記憶。無視する側と同じ規則で決める。"""
        rows = self._all(
            "SELECT e.* FROM episode e"
            + " LEFT JOIN episode_index i ON i.episode_id = e.id AND i.model = ?"
            + " WHERE e.dweller_id = ? AND i.episode_id IS NULL ORDER BY e.id",
            (model, dweller_id),
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

    def record_shift(self, dweller_id: str, shift: Shift) -> None:
        cursor = self._connection.execute(
            "INSERT INTO shift (dweller_id, at, delta, cause, episode_id) VALUES (?, ?, ?, ?, ?)"
            + " ON CONFLICT DO NOTHING RETURNING id",
            (dweller_id, shift.at.isoformat(), shift.delta, shift.cause, shift.episode_id),
        )
        inserted: sqlite3.Row | None = cursor.fetchone()  # pyright: ignore[reportAny]
        if inserted is not None or shift.episode_id is None:
            return
        row = self._one(
            "SELECT delta, cause FROM shift WHERE dweller_id = ? AND episode_id = ?",
            (dweller_id, shift.episode_id),
        )
        if row is None:
            raise RuntimeError(f"一往復 {shift.episode_id} の動きを書けなかった")
        if row.real("delta") != shift.delta or row.text("cause") != shift.cause:
            raise RememberingConflict(f"同じ一往復へ異なる気持ちの動きが届いた: {shift.episode_id}")

    def shifts(self, dweller_id: str) -> tuple[Shift, ...]:
        rows = self._all(
            "SELECT at, delta, cause, episode_id FROM shift WHERE dweller_id = ? ORDER BY id",
            (dweller_id,),
        )
        return tuple(
            Shift(
                at=datetime.fromisoformat(row.text("at")),
                delta=row.real("delta"),
                cause=row.text("cause"),
                episode_id=row.number_or_none("episode_id"),
            )
            for row in rows
        )

    def episodes_after(self, dweller_id: str, at: datetime | None) -> tuple[Episode, ...]:
        rows = self._all(
            "SELECT * FROM episode"
            + " WHERE dweller_id = ? AND happened_at > ? ORDER BY happened_at, id",
            (dweller_id, "" if at is None else at.isoformat()),
        )
        return tuple(self._as_episode(row) for row in rows)

    def record_dream(
        self,
        dweller_id: str,
        at: datetime,
        read_from: datetime,
        read_to: datetime,
        count: int,
        kept: int,
        noticing: str | None,
    ) -> Dream:
        cursor = self._connection.execute(
            "INSERT INTO dream (dweller_id, at, read_from, read_to, count, kept, noticing)"
            + " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dweller_id,
                at.isoformat(),
                read_from.isoformat(),
                read_to.isoformat(),
                count,
                kept,
                noticing,
            ),
        )
        self._connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("夢の番号が付かなかった")
        return Dream(cursor.lastrowid, at, read_from, read_to, count, kept, noticing)

    def latest_dream(self, dweller_id: str) -> Dream | None:
        row = self._one(
            "SELECT id, at, read_from, read_to, count, kept, noticing FROM dream"
            + " WHERE dweller_id = ? ORDER BY id DESC LIMIT 1",
            (dweller_id,),
        )
        if row is None:
            return None
        return Dream(
            id=row.number("id"),
            at=datetime.fromisoformat(row.text("at")),
            read_from=datetime.fromisoformat(row.text("read_from")),
            read_to=datetime.fromisoformat(row.text("read_to")),
            count=row.number("count"),
            kept=row.number("kept"),
            noticing=row.text_or_none("noticing"),
        )

    def record_gist(self, dweller_id: str, dream_id: int, gist: Gist) -> None:
        cursor = self._connection.execute(
            "INSERT INTO gist (dweller_id, dream_id, made_at, text) VALUES (?, ?, ?, ?)",
            (dweller_id, dream_id, gist.made_at.isoformat(), gist.text),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("要点の番号が付かなかった")
        _ = self._connection.executemany(
            "INSERT INTO gist_source (gist_id, episode_id) VALUES (?, ?)",
            [(cursor.lastrowid, episode_id) for episode_id in gist.sources],
        )
        self._connection.commit()

    def gists_of_dream(self, dream_id: int) -> tuple[Gist, ...]:
        rows = self._all(
            "SELECT id, made_at, text FROM gist WHERE dream_id = ? ORDER BY id", (dream_id,)
        )
        return tuple(
            Gist(
                text=row.text("text"),
                made_at=datetime.fromisoformat(row.text("made_at")),
                sources=tuple(
                    source.number("episode_id")
                    for source in self._all(
                        "SELECT episode_id FROM gist_source WHERE gist_id = ? ORDER BY episode_id",
                        (row.number("id"),),
                    )
                ),
            )
            for row in rows
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
        recalled = row.text_or_none("recalled_at")
        return Episode(
            id=row.number("id"),
            utterance=row.text("utterance"),
            reply=row.text("reply"),
            identity_version=row.number("identity_version"),
            happened_at=datetime.fromisoformat(row.text("happened_at")),
            recalled_at=None if recalled is None else datetime.fromisoformat(recalled),
            source=row.text_or_none("source"),
        )

    def _as_text(self, vector: Vector) -> str:
        """インデックスを文字として持つ。JSON を通すと境界で型が消える。"""
        return ",".join(repr(value) for value in vector)

    def _as_vector(self, row: Row) -> Vector:
        return tuple(float(part) for part in row.text("vector").split(","))

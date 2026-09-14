"""往復の予算と参照を一時保存する。検索失敗や再接続で予算を戻さない。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Collection, Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import final

from yadori.adapter.store.sqlite import Row
from yadori.domain.recall.model import (
    CALL_LIMIT,
    SEARCH_LIMIT,
    TEXT_LIMIT,
    Attempt,
    Candidates,
    Page,
    Problem,
    RecallFailure,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS binding (dweller TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    source TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, active INTEGER NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0, searches INTEGER NOT NULL DEFAULT 0,
    characters INTEGER NOT NULL DEFAULT 0, suggested TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refs (
    turn TEXT NOT NULL, token TEXT PRIMARY KEY, episode INTEGER NOT NULL,
    document TEXT, progress INTEGER NOT NULL DEFAULT 0, UNIQUE(turn,episode)
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY, turn TEXT NOT NULL, operation TEXT NOT NULL,
    request TEXT NOT NULL, response TEXT
);
"""


@dataclass(frozen=True)
class Ticket:
    turn: str
    attempt: int


@final
class RecallLedger:
    def __init__(self, run_dir: Path, dweller_id: str) -> None:
        self._path = run_dir / "recall.sqlite"
        if not run_dir.is_dir():
            raise RecallFailure("unavailable", "起動専用領域がありません")
        with self._connect() as connection:
            _ = connection.executescript(_SCHEMA)
            _ = connection.execute("BEGIN IMMEDIATE")
            row = self._one(connection, "SELECT dweller FROM binding", ())
            if row is None:
                _ = connection.execute("INSERT INTO binding VALUES (?)", (dweller_id,))
            elif row.text("dweller") != dweller_id:
                raise RecallFailure(
                    "unavailable", "この起動と人物の設定が一致しません。起動し直してください"
                )
        self._path.chmod(0o600)
        self.dweller_id: str = dweller_id

    def begin(self, source: str, suggested: Collection[int]) -> str:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            existing = self._one(connection, "SELECT * FROM turns WHERE source=?", (source,))
            if existing is not None:
                if existing.number("active") == 0:
                    raise RecallFailure("inactive_turn", "終了した往復は再開できません")
                return existing.text("token")
            _ = connection.execute("UPDATE turns SET active=0")
            token = uuid.uuid4().hex
            _ = connection.execute(
                "INSERT INTO turns(source,token,active,suggested) VALUES (?,?,1,?)",
                (source, token, json.dumps(sorted(set(suggested)))),
            )
            return token

    def end(self, source: str) -> None:
        with self._connect() as connection:
            _ = connection.execute("UPDATE turns SET active=0 WHERE source=?", (source,))

    def reserve(self, turn: str, operation: str, request: str) -> Ticket:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            current = self._active(connection, turn)
            if (
                current.number("calls") >= CALL_LIMIT
                or current.number("characters") >= TEXT_LIMIT
                or (operation == "search" and current.number("searches") >= SEARCH_LIMIT)
            ):
                raise RecallFailure(
                    "limit_reached",
                    "この往復では再試行せず、取得済みの根拠で答えるか不足点を確認してください",
                )
            _ = connection.execute(
                "UPDATE turns SET calls=calls+1, searches=searches+? WHERE token=?",
                (int(operation == "search"), turn),
            )
            cursor = connection.execute(
                "INSERT INTO attempts(turn,operation,request) VALUES (?,?,?)",
                (turn, operation, request),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("検索の予約番号がありません")
            return Ticket(turn, cursor.lastrowid)

    def publish(self, ticket: Ticket, result: Candidates | Page | Problem) -> str:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            current = self._active(connection, ticket.turn)
            data: dict[str, object] = asdict(result)
            data["calls_left"] = CALL_LIMIT - current.number("calls")
            data["searches_left"] = SEARCH_LIMIT - current.number("searches")
            if (
                isinstance(result, Page)
                and not result.complete
                and current.number("calls") == CALL_LIMIT
            ):
                data["status"] = "limit_reached"
                data["next_offset"] = None
                data["message"] = (
                    "全文未取得のまま今回の上限に達しました。参照と位置は次の往復へ引き継げません。"
                    + "続きを読めると約束せず、取得済みの範囲を伝えて"
                    + "不足する条件を本人へ確認してください。"
                )
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if current.number("characters") + len(text) > TEXT_LIMIT:
                _ = connection.execute(
                    "UPDATE turns SET characters=? WHERE token=?", (TEXT_LIMIT, ticket.turn)
                )
                text = json.dumps(
                    {
                        "status": "limit_reached",
                        "message": "この往復の応答量の上限です。再試行せず不足点を確認してください",
                    },
                    ensure_ascii=False,
                )
            else:
                _ = connection.execute(
                    "UPDATE turns SET characters=characters+? WHERE token=?",
                    (len(text), ticket.turn),
                )
                if isinstance(result, Page):
                    _ = connection.execute(
                        "UPDATE refs SET progress=MAX(progress,?) WHERE token=? AND turn=?",
                        (result.offset + len(result.text), result.reference, ticket.turn),
                    )
            _ = connection.execute(
                "UPDATE attempts SET response=? WHERE id=?", (text, ticket.attempt)
            )
            return text

    def history(self) -> tuple[Attempt, ...]:
        """起動中の検査用履歴。原文の記憶や思い出した回数とは別に持つ。"""
        with self._connect() as connection:
            rows: list[sqlite3.Row] = connection.execute(
                "SELECT * FROM attempts ORDER BY id"
            ).fetchall()
            return tuple(
                Attempt(
                    Row(row).text("turn"),
                    Row(row).text("operation"),
                    Row(row).text("request"),
                    Row(row).text_or_none("response"),
                )
                for row in rows
            )

    def references(self, ticket: Ticket) -> TurnReferences:
        return TurnReferences(self, ticket.turn)

    def suggested(self, turn: str) -> frozenset[int]:
        with self._connect() as connection:
            row = self._active(connection, turn)
            values: object = json.loads(row.text("suggested"))  # pyright: ignore[reportAny]
            if not isinstance(values, list):
                raise RecallFailure("unavailable", "自動提示候補を読めません")
            return frozenset(self._number(one) for one in values)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    def refer(self, turn: str, episode_id: int) -> str:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _ = self._active(connection, turn)
            token = uuid.uuid4().hex
            _ = connection.execute(
                "INSERT OR IGNORE INTO refs(turn,token,episode) VALUES (?,?,?)",
                (turn, token, episode_id),
            )
            row = self._one(
                connection, "SELECT token FROM refs WHERE turn=? AND episode=?", (turn, episode_id)
            )
            if row is None:
                raise RecallFailure("unavailable", "参照を残せません")
            return row.text("token")

    def document(self, turn: str, reference: str, make: Callable[[int], str]) -> tuple[str, int]:
        with self._connect() as connection:
            _ = self._active(connection, turn)
            row = self._reference(connection, turn, reference)
            text = row.text_or_none("document")
            if text is not None:
                return text, row.number("progress")
            episode_id = row.number("episode")
        text = make(episode_id)
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _ = self._active(connection, turn)
            _ = connection.execute(
                "UPDATE refs SET document=? WHERE token=? AND turn=? AND document IS NULL",
                (text, reference, turn),
            )
            row = self._reference(connection, turn, reference)
            return row.text("document"), row.number("progress")

    def _reference(self, connection: sqlite3.Connection, turn: str, reference: str) -> Row:
        row = self._one(connection, "SELECT * FROM refs WHERE token=?", (reference,))
        if row is None:
            raise RecallFailure("invalid_reference", "候補から返された参照を指定してください")
        if row.text("turn") != turn:
            raise RecallFailure("stale_reference", "前の往復の参照です。今回の候補を使ってください")
        return row

    def _active(self, connection: sqlite3.Connection, turn: str) -> Row:
        row = self._one(connection, "SELECT * FROM turns WHERE token=? AND active=1", (turn,))
        if row is None:
            raise RecallFailure("inactive_turn", "今回のフックで渡された往復キーを使ってください")
        return row

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _one(
        self, connection: sqlite3.Connection, sql: str, arguments: tuple[object, ...]
    ) -> Row | None:
        row: sqlite3.Row | None = connection.execute(sql, arguments).fetchone()  # pyright: ignore[reportAny]
        return None if row is None else Row(row)

    def _number(self, value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise RecallFailure("unavailable", "候補番号を読めません")
        return value


@final
class TurnReferences:
    def __init__(self, ledger: RecallLedger, turn: str) -> None:
        self._ledger = ledger
        self._turn = turn

    @property
    def suggested(self) -> frozenset[int]:
        return self._ledger.suggested(self._turn)

    def refer(self, episode_id: int) -> str:
        return self._ledger.refer(self._turn, episode_id)

    def document(self, reference: str, make: Callable[[int], str]) -> tuple[str, int]:
        return self._ledger.document(self._turn, reference, make)

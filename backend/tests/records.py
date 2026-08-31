"""テストで使う架空の記録と、答えを固定した判定。

利用者の実際の記録を使わない。Claude Code と Codex の記録の形は、実物を見て
写した最小の骨組みである（形が今も同じかは契約テストが確かめる）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from yadori.domain.evaluation import Asking, CannotDraft, Pair
from yadori.domain.memory import HowToRecall

WORKSPACE = "/home/someone/work/garden"
OTHER_WORKSPACE = "/home/someone/work/office"
START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _at(minute: int) -> str:
    return (START + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def claude_code_lines(
    session: str,
    workspace: str,
    turns: Sequence[tuple[str, str]],
    *,
    first_minute: int = 0,
) -> str:
    """Claude Code の形。発話と返事の組を、一分おきに並べる。"""
    rows: list[dict[str, object]] = []
    for offset, (spoken, reply) in enumerate(turns):
        minute = first_minute + offset * 2
        rows.append(
            {
                "type": "user",
                "sessionId": session,
                "cwd": workspace,
                "timestamp": _at(minute),
                "message": {"role": "user", "content": spoken},
            }
        )
        rows.append(
            {
                "type": "assistant",
                "sessionId": session,
                "cwd": workspace,
                "timestamp": _at(minute + 1),
                "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]},
            }
        )
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


def claude_code_tool_turn(
    session: str, workspace: str, spoken: str, reply: str, *, minute: int
) -> str:
    """道具を使ってから答える一往復。道具の結果は user の行として記録される。"""
    rows: list[dict[str, object]] = [
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute),
            "message": {"role": "user", "content": spoken},
        },
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 1),
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]},
        },
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 2),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "..."}]},
        },
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 3),
            "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]},
        },
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


def claude_code_noise(session: str, workspace: str, *, minute: int) -> str:
    """Claude Code の形に混ざる雑音。命令、道具の結果、画像、割り込み、子エージェントへの指示。"""
    rows: list[dict[str, object]] = [
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "isSidechain": True,
            "timestamp": _at(minute - 1),
            "message": {
                "role": "user",
                "content": "あなたは検査役です。次の差分を読んで指摘を重大度順に並べてください。",
            },
        },
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute),
            "message": {
                "role": "user",
                "content": "<command-name>/help</command-name> 使い方を見る",
            },
        },
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 1),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "貼り付けた出力がここに長く続く"}],
            },
        },
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 2),
            "message": {"role": "user", "content": [{"type": "image", "source": {}}]},
        },
        {
            "type": "user",
            "sessionId": session,
            "cwd": workspace,
            "timestamp": _at(minute + 3),
            "message": {"role": "user", "content": "[Request interrupted by user]"},
        },
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


def codex_lines(
    session: str,
    workspace: str,
    turns: Sequence[tuple[str, str]],
    *,
    first_minute: int = 0,
) -> str:
    """Codex の形。先頭に session_meta、続いて発言。"""
    rows: list[dict[str, object]] = [
        {
            "timestamp": _at(first_minute),
            "type": "session_meta",
            "payload": {"id": session, "cwd": workspace, "cli_version": "0.0"},
        }
    ]
    for offset, (spoken, reply) in enumerate(turns):
        minute = first_minute + offset * 2
        rows.append(
            {
                "timestamp": _at(minute),
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": spoken}],
                },
            }
        )
        rows.append(
            {
                "timestamp": _at(minute + 1),
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": reply}],
                },
            }
        )
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


def codex_noise(session: str, workspace: str, *, minute: int) -> str:
    """Codex の形に混ざる雑音。道具が入れる作業場所の説明。"""
    meta: dict[str, object] = {
        "timestamp": _at(minute),
        "type": "session_meta",
        "payload": {"id": session, "cwd": workspace, "cli_version": "0.0"},
    }
    row: dict[str, object] = {
        "timestamp": _at(minute),
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "<environment_context>cwd など</environment_context>",
                }
            ],
        },
    }
    return json.dumps(meta, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False) + "\n"


def write(place: Path, name: str, text: str) -> Path:
    place.mkdir(parents=True, exist_ok=True)
    path = place / name
    _ = path.write_text(text, encoding="utf-8")
    return path


@final
class FixedJudge:
    """答えを固定した判定。後の発話の文言から、同じ話題とする前の発話の文言を決める。

    渡された問い（発話と候補）を記録する。候補に無い前の発話は組にできない。
    """

    def __init__(self, pointing: Mapping[str, Sequence[str]]) -> None:
        self._pointing: Mapping[str, Sequence[str]] = pointing
        self.askings: list[Asking] = []

    def pairs(self, askings: Sequence[Asking]) -> tuple[Pair, ...]:
        self.askings.extend(askings)
        found: list[Pair] = []
        for later, asking in enumerate(askings):
            for earlier, candidate in enumerate(asking.candidates):
                if candidate in self._pointing.get(asking.utterance, ()):
                    found.append(Pair(later=later, earlier=earlier))
        return tuple(found)


@final
class FailingJudge:
    def __init__(self, reason: str) -> None:
        self._reason: str = reason

    def pairs(self, askings: Sequence[Asking]) -> tuple[Pair, ...]:
        del askings
        raise CannotDraft(self._reason)


# テストで候補を引く条件。文字の埋め込みは語が重なる組を 0.2 前後で拾う。
TEST_HOW = HowToRecall(recent_turns=2, found_limit=10, relevance_floor=0.15)


def rows_of(loaded: dict[str, object], key: str) -> list[dict[str, object]]:
    """読んだ評価セットの表を、型を確かめながら取り出す。"""
    rows = loaded.get(key, [])
    assert isinstance(rows, list)
    found: list[dict[str, object]] = []
    for row in rows:  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(row, dict)
        found.append(row)  # pyright: ignore[reportUnknownArgumentType]
    return found


def text_of(row: dict[str, object], key: str) -> str:
    value = row[key]
    assert isinstance(value, str)
    return value


def names_of(row: dict[str, object], key: str) -> list[str]:
    """名前の並び。overlap のように名前を鍵に持つ表なら、その鍵を返す。"""
    value: object = row.get(key, [])
    assert isinstance(value, (list, dict))
    found: list[str] = []
    for name in value:  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(name, str)
        found.append(name)
    return found

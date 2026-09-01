"""今の状態と動きの時系列を読む。

気持ちと性格は動きから求めるため、時点を指せばその時点の値を求め直せる（ADR-007）。
ここは保存先を開いて読み、書く。値を求める規則は domain が持つ。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, final

from yadori.adapter.store import SqliteMemories
from yadori.domain.memory import (
    CHARACTER_HALF_LIFE,
    CHARACTER_WEIGHT,
    MOOD_HALF_LIFE,
    Memories,
    Shift,
    State,
)
from yadori.infrastructure.settings import NotSettled, SettingsFile


@final
class StateReport:
    """`state` の入口。いまの（または指した時点の）気持ちと性格と、動きの時系列を書く。"""

    def __init__(
        self, home: Path | None = None, at: datetime | None = None, writing: TextIO | None = None
    ) -> None:
        self._settings_file: SettingsFile = SettingsFile(home)
        self._at: datetime | None = at
        self._writing: TextIO = writing or sys.stdout

    def run(self) -> int:
        """読んで書く。宿りの設定が無ければ理由を書いて 1。"""
        try:
            settings = self._settings_file.read()
        except NotSettled as missing:
            print(missing, file=sys.stderr)
            return 1
        memories = SqliteMemories(settings.memories_path)
        try:
            self._write(memories, settings.dweller.id)
        finally:
            memories.close()
        return 0

    def _write(self, memories: Memories, dweller_id: str) -> None:
        """時点までの動きから値を求め、値と動きの並びを書く。"""
        now = self._at or datetime.now(UTC)
        shifts = tuple(one for one in memories.shifts(dweller_id) if one.at <= now)
        state = State.from_shifts(shifts, now)
        when = "" if self._at is None else f"（{self._at.isoformat()} 時点）"
        self._say(
            f"気持ち: {state.mood.value:+.2f} {state.mood.described}"
            + (when or f"（半減期 {self._hours(MOOD_HALF_LIFE.total_seconds())}）")
        )
        self._say(
            f"性格: {state.character.value:+.2f} {state.character.described}"
            + (
                when
                or f"（半減期 {CHARACTER_HALF_LIFE.days} 日、動きは {CHARACTER_WEIGHT:g} 倍で効く）"
            )
        )
        if not shifts:
            self._say("動きはまだありません")
            return
        self._say("動き（新しい順）:")
        for shift in reversed(shifts):
            self._say(self._line(memories, shift))

    def _line(self, memories: Memories, shift: Shift) -> str:
        episode = None if shift.episode_id is None else memories.episode(shift.episode_id)
        spoken = "" if episode is None else f"  「{episode.utterance}」"
        return (
            f"  {shift.at.astimezone():%Y-%m-%d %H:%M}  {shift.delta:+.1f}  {shift.cause}{spoken}"
        )

    def _hours(self, seconds: float) -> str:
        return f"{seconds / 3600:g} 時間"

    def _say(self, line: str) -> None:
        _ = self._writing.write(f"{line}\n")

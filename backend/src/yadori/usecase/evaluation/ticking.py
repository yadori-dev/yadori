"""使い捨ての記憶の時刻。測るときも下書きを作るときも、結果を実際の時刻に左右させない。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final


@final
class Ticking:
    def __init__(self) -> None:
        self._at: datetime = datetime(2000, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._at += timedelta(minutes=1)
        return self._at

"""話す場所に求めること。

宿りは常駐する本体で、話す場所はその出ていく先である（ADR-002）。起動はどの場所で待つかを
受け取るだけで、場所ごとの違いは知らない。
"""

from __future__ import annotations

from typing import Protocol


class Place(Protocol):
    """宿りが話す場所。話しかけられるのを待ち、一往復ずつ通す。"""

    def listen(self) -> None: ...

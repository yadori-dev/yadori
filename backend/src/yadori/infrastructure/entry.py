"""端末から呼ばれたときの入口。

会話と測るで起動が二つあるため、ここで選ぶ。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import final

from yadori.domain.memory import HowToRecall
from yadori.infrastructure.measure import Measure
from yadori.infrastructure.start import Startup

USAGE = (
    "使い方:\n"
    + "  python -m yadori                    宿りを起こして話す\n"
    + "  python -m yadori measure            今の条件で測る\n"
    + "  python -m yadori measure [--eval PATH] [--floor N] [--recent N] [--limit N]\n"
    + "                                      条件を変えて測り、件ごとの差を出す\n"
    + "\n"
    + "  --eval を省くと evals/recall.toml を測る。実際の会話から作った評価\n"
    + "  セットは手元に置き、--eval で指す。リポジトリへ入れない。"
)


@final
class Entry:
    def __init__(self, argv: list[str]) -> None:
        self._argv: list[str] = argv[1:]

    def run(self) -> int:
        """何をするかを選んで渡す。

        - 引数が無ければ話す
        - measure なら測る
        - それ以外は使い方を書く
        """
        if not self._argv:
            return Startup().run()
        if self._argv[0] == "measure":
            return self._measure()
        print(USAGE, file=sys.stderr)
        return 1

    def _measure(self) -> int:
        rest = self._argv[1:]
        if len(rest) % 2 != 0:
            print(USAGE, file=sys.stderr)
            return 1
        given = dict(zip(rest[::2], rest[1::2], strict=True))
        eval_path = self._eval_path(given)
        if not given:
            return Measure(eval_path=eval_path).run()
        changed = self._changed(given)
        if changed is None:
            print(USAGE, file=sys.stderr)
            return 1
        return Measure(eval_path=eval_path, changed=changed).run()

    def _eval_path(self, given: dict[str, str]) -> Path | None:
        """どの評価セットを測るか。省けばリポジトリの架空のものを測る。"""
        written = given.pop("--eval", None)
        return None if written is None else Path(written)

    def _changed(self, given: dict[str, str]) -> HowToRecall | None:
        """比べる相手の条件。読めない書き方なら何も返さない。"""
        now = HowToRecall()
        floor, recent, limit = now.relevance_floor, now.recent_turns, now.found_limit
        for name, value in given.items():
            try:
                if name == "--floor":
                    floor = float(value)
                elif name == "--recent":
                    recent = int(value)
                elif name == "--limit":
                    limit = int(value)
                else:
                    return None
            except ValueError:
                return None
        return HowToRecall(recent_turns=recent, found_limit=limit, relevance_floor=floor)

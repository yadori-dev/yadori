"""端末から呼ばれたときの入口。

会話と測るで起動が二つあるため、ここで選ぶ。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import final

from yadori.adapter.embedding import Choosing, NotAnEmbeddingName, Weighing
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.measure import Measure
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup

USAGE = (
    "使い方:\n"
    + "  python -m yadori                    宿りを起こして話す\n"
    + "  python -m yadori measure            今の条件で測る\n"
    + "  python -m yadori measure [--eval PATH] [--embedding NAME(+NAME)]\n"
    + "                          [--floor N] [--recent N] [--limit N]\n"
    + "                                      条件を変えて測り、問ごとの差を出す\n"
    + "\n"
    + "  --eval を省くと evals/recall.toml を測る。実際の会話から作った評価\n"
    + "  セットは手元に置き、--eval で指す。リポジトリへ入れない。\n"
    + "  --embedding は characters、multilingual、試す埋め込みの名前、埋め込みを\n"
    + "  動かす道具の対応表にある 配布元/名前 を指せる。+ で並べると両方の道から\n"
    + "  渡す。省くと既定の埋め込みで測る。結果には出自、添え書き、大きさ、\n"
    + "  読み込みと一発話の時間が並ぶ。\n"
    + "\n"
    + "  python -m yadori evals draft --from PATH [--from PATH] --out FILE [--append]\n"
    + "                                      対話する道具の記録から、評価セットの\n"
    + "                                      下書きを作る。問は人が確かめるまで測れない。\n"
    + "                                      --append は既にある下書きへ、前回の範囲より\n"
    + "                                      後に増えた記録の分だけを足す。前回の問と確認は\n"
    + "                                      変えない。前回の範囲を持つ下書きにだけ足せる\n"
    + "\n"
    + "  --from は Claude Code の記録のディレクトリ（~/.claude/projects）や Codex の\n"
    + "  記録のディレクトリ（~/.codex/sessions）を指す。後の発話ごとに、宿りの思い出す\n"
    + "  仕組みで前の発話の候補を引き、その発話と候補だけを判定のため手元の\n"
    + "  Claude Code へ渡す。Claude Code の記録は普段と同じ相手へ渡るが、Codex の\n"
    + "  記録は判定のために別の相手へ渡ることになる。返事、時刻、作業場所は渡らず、\n"
    + "  記録を丸ごと渡すこともない。思い出す仕組みが拾えなかった組は下書きに\n"
    + "  出ないので手で足す。直近の範囲の組は測れないので足さない。--out は\n"
    + "  リポジトリの外を指す。--append を付けないときは、既にあるファイルには書かない。"
)


@final
class Entry:
    def __init__(self, argv: list[str], choosing: Choosing | None = None) -> None:
        self._argv: list[str] = argv[1:]
        # 会話と同じ `YADORI_HOME` の下の `models/` を取得先にし、取得の前触れは標準出力へ出す。
        # 測った結果の書き先とは別である。
        self._choosing: Choosing = choosing or Choosing(
            SettingsFile().models_path, announcing=print
        )

    def run(self) -> int:
        """何をするかを選んで渡す。

        - 引数が無ければ話す
        - measure なら測る
        - evals draft なら記録から評価セットの下書きを作る
        - それ以外は使い方を書く
        """
        if not self._argv:
            return Startup().run()
        if self._argv[0] == "measure":
            return self._measure()
        if self._argv[:2] == ["evals", "draft"]:
            return self._draft()
        print(USAGE, file=sys.stderr)
        return 1

    def _draft(self) -> int:
        """記録のディレクトリと出力先を読み取り、下書きを作る。読めない書き方なら使い方を書く。"""
        # 値を取らない引数（--append）だけを先に拾い、残りを名前と値の対として読む。
        rest = [given for given in self._argv[2:] if given != "--append"]
        append = len(self._argv[2:]) - len(rest)
        if append > 1:
            print(USAGE, file=sys.stderr)
            return 1
        places: list[Path] = []
        out: Path | None = None
        for name, value in zip(rest[::2], rest[1::2], strict=False):
            if name == "--from":
                places.append(Path(value))
            elif name == "--out" and out is None:
                out = Path(value)
            else:
                print(USAGE, file=sys.stderr)
                return 1
        if len(rest) % 2 != 0 or not places or out is None:
            print(USAGE, file=sys.stderr)
            return 1
        return Drafter(places, out, append=append == 1).run()

    def _measure(self) -> int:
        rest = self._argv[1:]
        if len(rest) % 2 != 0:
            print(USAGE, file=sys.stderr)
            return 1
        given = dict(zip(rest[::2], rest[1::2], strict=True))
        eval_path = self._eval_path(given)
        written = given.pop("--embedding", None)
        changed = None
        if given:
            changed = self._changed(given)
            if changed is None:
                print(USAGE, file=sys.stderr)
                return 1
        try:
            embeddings = self._embeddings(written)
        except (NotAnEmbeddingName, EmbeddingsUnavailable) as reason:
            # 名前の書き方ではなく中身の誤りなので、使い方ではなく理由を出す。
            print(f"測れません: {reason}", file=sys.stderr)
            return 1
        return Measure(embeddings, eval_path=eval_path, changed=changed).run()

    def _embeddings(self, written: str | None) -> Weighing | list[Weighing]:
        """名前から埋め込みを選ぶ。"""
        return self._choosing.pick(written)

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

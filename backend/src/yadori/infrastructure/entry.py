"""端末から呼ばれたときの入口。

会話と測るで起動が二つあるため、ここで選ぶ。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import final

import tomlkit

from yadori.adapter.embedding import Choosing, NotAnEmbeddingName, Weighing
from yadori.adapter.place import DiscordGateway, DiscordPlace
from yadori.adapter.tool import ToolCallFailed
from yadori.domain.memory import EmbeddingsUnavailable, HowToRecall
from yadori.infrastructure.agy import AgyCompanion, AgyHook
from yadori.infrastructure.claude import ClaudeCompanion, ClaudeHook
from yadori.infrastructure.codex import CodexCompanion, CodexHook
from yadori.infrastructure.draft import Drafter
from yadori.infrastructure.dream import Dreamer
from yadori.infrastructure.measure import Measure
from yadori.infrastructure.setting import Setting
from yadori.infrastructure.settings import Configuration, NotSettled, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.infrastructure.state import StateReport
from yadori.infrastructure.tools import ToolChoice

USAGE = (
    "使い方:\n"
    + "  yadori                            共通の優先順で道具を選び、宿りとして起こす\n"
    + "  yadori setting                    名前・性格・人物・道具順を対話で設定する\n"
    + "  yadori chat                       専用の端末チャットで話す\n"
    + "  yadori claude                      宿りとして Claude Code を起こす\n"
    + "  yadori agy                         宿りとして agy を起こす\n"
    + "  yadori codex                       宿りとして Codex を起こす\n"
    + "  python -m yadori discord            Discord で話しかけられるのを待つ。トークンは\n"
    + "                                      YADORI_HOME の discord.toml に置く\n"
    + "  python -m yadori dream              前回の夢より後の記憶を読み直し、気づきを一つ残す\n"
    + "  python -m yadori state [--at 時刻]  いまの気持ちと性格と、動きの時系列を読む。\n"
    + "                                      --at に ISO 形式の時刻を指すと、その時点の値\n"
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
    + "  共通の優先順で選んだ道具へ渡す。記録元とは別の相手に渡ることがある。\n"
    + "  選んだ相手は実行時に表示する。返事、時刻、作業場所は渡らず、\n"
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

        - 引数が無ければ共通の優先順で対話する道具を起こす
        - measure なら測る
        - evals draft なら記録から評価セットの下書きを作る
        - それ以外は使い方を書く
        """
        if not self._argv or self._argv[0] not in {"_claude-hook", "_codex-hook", "_agy-hook"}:
            _ = os.environ.pop("YADORI_SETTINGS_SNAPSHOT", None)
        if self._argv in (["--help"], ["-h"]):
            print(USAGE)
            return 0
        if self._argv == ["setting"]:
            return Setting().run()
        if not self._argv or self._argv in (["claude"], ["codex"], ["agy"], ["chat"]):
            return self._launch()
        if len(self._argv) == 3 and self._argv[0] == "_claude-hook":
            return ClaudeHook(self._argv[1], Path(self._argv[2])).run()
        if len(self._argv) == 3 and self._argv[0] == "_agy-hook":
            return AgyHook(self._argv[1], Path(self._argv[2])).run()
        if len(self._argv) == 3 and self._argv[0] == "_codex-hook":
            return CodexHook(self._argv[1], Path(self._argv[2])).run()
        if self._argv[0] == "measure":
            return self._measure()
        if self._argv[0] == "state":
            return self._state()
        if self._argv == ["discord"]:
            return self._discord()
        if self._argv == ["dream"]:
            return Dreamer().run()
        if self._argv[:2] == ["evals", "draft"]:
            return self._draft()
        print(USAGE, file=sys.stderr)
        return 1

    def _launch(self) -> int:
        if not Setting().ensure():
            return 1
        try:
            explicit = self._argv[0] if self._argv else None
            if explicit == "chat":
                return Startup().run()
            name = ToolChoice().select("対話起動", explicit=explicit)
            configuration = Configuration().load()
            configuration.home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=configuration.home, prefix="settings-run-") as run:
                snapshot = Path(run).resolve() / "settings.toml"
                _ = snapshot.write_text(tomlkit.dumps(configuration.data), encoding="utf-8")
                before = os.environ.get("YADORI_SETTINGS_SNAPSHOT")
                os.environ["YADORI_SETTINGS_SNAPSHOT"] = str(snapshot)
                try:
                    if name == "claude":
                        return ClaudeCompanion().run()
                    if name == "codex":
                        return CodexCompanion().run()
                    return AgyCompanion().run()
                finally:
                    if before is None:
                        _ = os.environ.pop("YADORI_SETTINGS_SNAPSHOT", None)
                    else:
                        os.environ["YADORI_SETTINGS_SNAPSHOT"] = before
        except (NotSettled, ToolCallFailed, OSError) as trouble:
            print(trouble, file=sys.stderr)
            return 1

    @staticmethod
    def console() -> int:
        """`yadori` 命令から呼ばれる入口。"""
        return Entry(sys.argv).run()

    def _discord(self) -> int:
        """Discord で待つ。トークンが無ければ、書き方を添えて断る。"""
        try:
            connection = SettingsFile().discord()
        except NotSettled as missing:
            print(missing, file=sys.stderr)
            return 1
        print("Discord の設定変更は再起動後に反映します", file=sys.stderr)
        return Startup().run(
            lambda turn, settings: DiscordPlace(
                turn,
                settings.dweller,
                connection.owner_id,
                DiscordGateway(connection.token),
            )
        )

    def _state(self) -> int:
        """時点を読み取り、状態を書く。読めない書き方なら使い方を書く。"""
        rest = self._argv[1:]
        if not rest:
            return StateReport().run()
        if len(rest) != 2 or rest[0] != "--at":
            print(USAGE, file=sys.stderr)
            return 1
        try:
            at = datetime.fromisoformat(rest[1])
        except ValueError:
            print(USAGE, file=sys.stderr)
            return 1
        # 時差の無い時刻は手元の時刻とみなす。
        return StateReport(at=at if at.tzinfo else at.astimezone()).run()

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

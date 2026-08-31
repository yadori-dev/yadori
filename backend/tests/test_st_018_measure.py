"""INC-018 のシステムテスト。外の入口から測る。

架空の会話で書く。利用者の実際の会話を使わない。
"""

from __future__ import annotations

import io
from pathlib import Path

from yadori.adapter.embedding import CharacterPairs
from yadori.domain.memory import HowToRecall
from yadori.infrastructure.measure import Measure

EVAL = """
within = 3

[[exchange]]
name = "tomato"
utterance = "ベランダにトマトの苗を植えました"
reply = "いいですね。"

[[exchange]]
name = "train"
utterance = "電車が遅れて会議に遅れました"
reply = "大変でしたね。"

[[exchange]]
name = "books"
utterance = "図書館で小説を三冊借りました"
reply = "楽しみですね。"

[[exchange]]
name = "movie"
utterance = "昨日は古い映画を観ました"
reply = "どんなお話でしたか。"

[[exchange]]
name = "laundry"
utterance = "洗濯物がよく乾きました"
reply = "よいお天気でしたね。"

[[exchange]]
name = "keyboard"
utterance = "新しい鍵盤楽器が届きました"
reply = "気になります。"

[[exchange]]
name = "dentist"
utterance = "歯医者の予約を取りました"
reply = "よかったですね。"

[[exchange]]
name = "neighbor"
utterance = "近所で工事が始まるそうです"
reply = "音が気になりそうですね。"

[[exchange]]
name = "coffee"
utterance = "豆を挽いて珈琲を淹れました"
reply = "香りがよさそうです。"

[[case]]
name = "引ける"
utterance = "トマトはその後どうなりましたか"
expected = ["tomato"]
forbidden = []

[[case]]
name = "混ざる"
utterance = "会議はどうなりましたか"
expected = []
forbidden = ["train"]
"""

# 語の重なりを見る埋め込みに合う条件。既定は埋め込みごとに違うため固定する。
BASELINE = HowToRecall(recent_turns=6, found_limit=5, relevance_floor=0.21)
# 締めると、引ける問が出なくなり、混ざる問の混入が消える。
TIGHTER = HowToRecall(recent_turns=6, found_limit=5, relevance_floor=0.35)


class TestMeasure:
    def _written(self, path: Path, changed: HowToRecall | None = None) -> tuple[str, int]:
        writing = io.StringIO()
        # 測る仕組みを確かめるため、埋め込みは固定する。替えると数が変わる。
        code = Measure(
            eval_path=path,
            baseline=BASELINE,
            changed=changed,
            embeddings=CharacterPairs(),
            writing=writing,
        ).run()
        return writing.getvalue(), code

    def _eval_at(self, tmp_path: Path, body: str = EVAL) -> Path:
        path = tmp_path / "recall.toml"
        _ = path.write_text(body, encoding="utf-8")
        return path

    # ST-018-001 問ごとの順位と近さが読める

    def test_ST_018_001_問ごとの結果と要約が出る(self, tmp_path: Path) -> None:
        written, code = self._written(self._eval_at(tmp_path))

        assert code == 0
        assert "2問中 1問で期待したやりとりが上位3件に入った" in written
        # 出てはいけないやりとりが出た問は、その順位まで読める。
        assert "出てはいけないやりとりが出た問: 1問" in written
        assert "混入: train 2位" in written

    # ST-018-003 良くなった問と悪くなった問を名指しできる

    def test_ST_018_003_良くなった問と悪くなった問が別々に出る(self, tmp_path: Path) -> None:
        first, _ = self._written(self._eval_at(tmp_path))
        both, code = self._written(self._eval_at(tmp_path), changed=TIGHTER)

        assert code == 0
        assert first in both
        # 全体では満たした数が変わらなくても、悪くなった問が消えない。
        assert "良くなった: 混ざる" in both
        assert "悪くなった: 引ける" in both
        assert "tomato 出ず" in both

    # ST-018-004 欠けたまま測らない

    def test_ST_018_004_無いやりとりを指すと数値が出ない(self, tmp_path: Path) -> None:
        broken = EVAL.replace('expected = ["tomato"]', 'expected = ["nothing"]')

        written, code = self._written(self._eval_at(tmp_path, broken))

        assert code == 1
        assert written == ""

    def test_ST_018_004_評価セットが無ければ数値が出ない(self, tmp_path: Path) -> None:
        written, code = self._written(tmp_path / "ない.toml")

        assert code == 1
        assert written == ""

"""INC-018 の結合テスト。測る手順の境界を確かめる。

架空の会話で書く。利用者の実際の会話を使わない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.evaluation import CannotMeasure, Case, Exchange, RecallEval
from yadori.domain.memory import Dweller, HowToRecall, Memories, Vector
from yadori.usecase.evaluation import Comparing, Measuring

EXCHANGES = (
    Exchange("tomato", "ベランダにトマトの苗を植えました", "いいですね。"),
    Exchange("tax", "住民税の納付書が届きました", "期限をお忘れなく。"),
    Exchange("train", "電車が遅れて会議に遅れました", "大変でしたね。"),
    Exchange("books", "図書館で小説を三冊借りました", "楽しみですね。"),
    Exchange("movie", "昨日は古い映画を観ました", "どんなお話でしたか。"),
    Exchange("laundry", "洗濯物がよく乾きました", "よいお天気でしたね。"),
    Exchange("keyboard", "新しい鍵盤楽器が届きました", "気になります。"),
    Exchange("dentist", "歯医者の予約を取りました", "よかったですね。"),
)
CASES = (
    Case("引ける", "トマトはその後どうなりましたか", ("tomato",), ()),
    Case("混ざる", "会議はどうなりましたか", (), ("train",)),
    Case("変わらない", "為替の見通しはどうでしょうか", (), ("books",)),
)
RECALL_EVAL = RecallEval(within=3, exchanges=EXCHANGES, cases=CASES)

# 下限を締めた条件と緩めた条件。緩めると一件が良くなり、別の一件が悪くなる。
TIGHT = HowToRecall(recent_turns=4, found_limit=5, relevance_floor=0.30)
LOOSE = HowToRecall(recent_turns=4, found_limit=5, relevance_floor=0.20)

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")


@final
class _WithoutIndex:
    """索引を書かない保存先。ほかの操作は本物へ渡す。"""

    def __init__(self, inner: InMemoryMemories) -> None:
        self._inner: InMemoryMemories = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)  # pyright: ignore[reportAny]

    def write_index(self, episode_id: int, model: str, vector: Vector) -> None:
        del episode_id, model, vector


class TestMeasuring:
    def _measuring(self, recall_eval: RecallEval | None = None) -> Measuring:
        return Measuring(recall_eval or RECALL_EVAL, InMemoryMemories, CharacterPairs())

    # IT-018-001 持ち主の記憶に触れず、何度測っても同じになる

    def test_IT_018_001_二度測っても同じ結果になる(self) -> None:
        measuring = self._measuring()

        assert measuring.at(TIGHT) == measuring.at(TIGHT)

    def test_IT_018_001_持ち主の記憶を開かない(self) -> None:
        owned = SqliteMemories(":memory:")
        owned.settle(SORA)
        _ = owned.write_identity(SORA.id, "わたしはそらです。")
        _ = owned.write_episode(SORA.id, "こんにちは", "はい", 1, datetime.now(UTC))
        kept = owned.count_episodes(SORA.id)

        _ = self._measuring().at(TIGHT)

        assert owned.count_episodes(SORA.id) == kept
        assert owned.retrieval(1).count == 0
        owned.close()

    # IT-018-002 要約が件ごとの結果と一致し、差を名指しできる

    def test_IT_018_002_要約が件ごとの結果と一致する(self) -> None:
        measured = self._measuring().at(LOOSE)

        assert measured.total == len(CASES)
        assert measured.met == sum(
            1 for outcome in measured.outcomes if outcome.met(measured.within)
        )
        assert measured.intruded == sum(
            1
            for outcome in measured.outcomes
            if any(one.rank is not None for one in outcome.forbidden)
        )

    def test_IT_018_002_良くなった件と悪くなった件を名指しし変わらない件は出さない(self) -> None:
        measuring = self._measuring()

        difference = Comparing(measuring.at(TIGHT), measuring.at(LOOSE)).difference()

        assert [shifted.case for shifted in difference.better] == ["引ける"]
        assert [shifted.case for shifted in difference.worse] == ["混ざる"]
        named = {shifted.case for shifted in difference.better + difference.worse}
        assert "変わらない" not in named

    def test_IT_018_002_全体では良くなっても悪くなった件が消えない(self) -> None:
        measuring = self._measuring()
        before = measuring.at(TIGHT)
        after = measuring.at(LOOSE)

        difference = Comparing(before, after).difference()

        # 満たした件の数は変わらないが、中身は入れ替わっている。
        assert before.met == after.met
        assert difference.worse != ()

    # IT-018-003 欠けていれば一件も測らない

    def test_IT_018_003_無いやりとりを指すと測らない(self) -> None:
        broken = RecallEval(
            within=3,
            exchanges=EXCHANGES,
            cases=(Case("壊れ", "なにか", ("nothing",), ()),),
        )

        with pytest.raises(CannotMeasure, match="無いやりとりを指している"):
            _ = self._measuring(broken).at(TIGHT)

    def test_IT_018_003_期待と禁止に同じやりとりを指すと測らない(self) -> None:
        broken = RecallEval(
            within=3,
            exchanges=EXCHANGES,
            cases=(Case("壊れ", "なにか", ("tomato",), ("tomato",)),),
        )

        with pytest.raises(CannotMeasure, match="期待と禁止に指している"):
            _ = self._measuring(broken).at(TIGHT)

    def test_IT_018_003_索引が欠けると測らない(self) -> None:
        def fresh() -> Memories:
            return _WithoutIndex(InMemoryMemories())  # pyright: ignore[reportReturnType]

        with pytest.raises(CannotMeasure, match="索引を持たない"):
            _ = Measuring(RECALL_EVAL, fresh, CharacterPairs()).at(TIGHT)

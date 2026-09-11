"""INC-057 のシステムテスト。

夢が前回より後の記憶から残すものを選び、要点の層を作り、選んだものをなぞり、原文は減らず、
要点が応対に渡る。架空の会話で書く。要点を書く相手は差し替え、AIモデルを呼ばない。
"""

from __future__ import annotations

import io
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from tests.sora import fixed
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.adapter.voice import ClaudeCodeVoice
from yadori.domain.dream import CannotDream, Summarized
from yadori.domain.memory import Episode, HowToRecall, Identity, Moved
from yadori.infrastructure.dream import Dreamer
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

AT = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
PAIRS = CharacterPairs()


@final
class _Summarizing:
    """決めた要点を返し、渡された往復を覚える。"""

    def __init__(
        self, gists: tuple[str, ...] = ("要点その一", "要点その二"), noticing: str | None = None
    ) -> None:
        self._gists: tuple[str, ...] = gists
        self._noticing: str | None = noticing
        self.read: list[tuple[Episode, ...]] = []

    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        del identity
        self.read.append(tuple(episodes))
        return Summarized(gists=self._gists, noticing=self._noticing)


@final
class _Failing:
    def summarize(self, identity: Identity, episodes: Sequence[Episode]) -> Summarized:
        del identity, episodes
        raise CannotDream("道具が応えない")


@final
class _Answering:
    """道具の口の代わり。決めた文を返し、渡された前置きを覚える。"""

    def __init__(self, answered: str = "はい。") -> None:
        self._answered: str = answered
        self.prefaces: list[str] = []

    def ask(self, preface: str, spoken: str) -> str:
        del spoken
        self.prefaces.append(preface)
        return self._answered


def _home(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n', encoding="utf-8"
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    return tmp_path


def _spoken(home: Path, turns: list[tuple[str, Moved | None]], start: datetime) -> None:
    """往復を積む。動きは渡されたときだけ。"""
    settings = SettingsFile(home).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(home).settle(memories, settings)
    at = [start]

    def clock() -> datetime:
        at[0] += timedelta(minutes=1)
        return at[0]

    conversation = Conversation(memories, PAIRS, clock)
    for said, moved in turns:
        _ = conversation.remember("sora", said, "はい", moved)
    memories.close()


def _recalled(home: Path, said: str, at: datetime) -> None:
    """思い出す口を呼び、思い出した記録を積む。"""
    settings = SettingsFile(home).read()
    memories = SqliteMemories(settings.memories_path)
    # 語の重なりの埋め込みに合う下限で、直近を除かずに思い出す（直近に入ると探されない）。
    _ = Conversation(memories, PAIRS, lambda: at, HowToRecall(0, 5, 0.4)).recall("sora", said)
    memories.close()


def _dream(home: Path, summarizing: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    real = sys.stderr
    sys.stderr = err
    try:
        code = Dreamer(
            home,
            summarizing=summarizing,  # pyright: ignore[reportArgumentType]
            default=fixed(PAIRS),
            how=HowToRecall(6, 5, 0.4),
            writing=out,
        ).run()
    finally:
        sys.stderr = real
    return code, out.getvalue(), err.getvalue()


def _opened(home: Path) -> SqliteMemories:
    return SqliteMemories(home / "memories.sqlite")


class TestST057001:
    """選ぶ基準と範囲。"""

    def test_ST_057_001_当たる往復だけが選ばれ無ければ範囲だけ残り新しくなければ読まない(
        self, tmp_path: Path
    ) -> None:
        home = _home(tmp_path / "home")
        _spoken(
            home,
            [
                ("図書館で小説を三冊借りました", None),  # 後で思い出される
                ("やっと通った", Moved(0.5, "ほっとした")),  # 大きく動いた
                ("トマトの苗を植えました", None),  # 同じ話題が繰り返す
                ("トマトの苗に水をやりました", None),
                ("洗濯物がよく乾きました", None),  # どれにも当たらない
            ],
            AT,
        )
        _recalled(home, "図書館で借りた小説", AT + timedelta(hours=1))
        summarizing = _Summarizing()

        first = _dream(home, summarizing)
        _spoken(home, [("鍵盤楽器が届きました", None)], AT + timedelta(hours=2))
        second = _dream(home, summarizing)
        third = _dream(home, summarizing)

        assert first[0] == 0 and "5 件を読み、4 件を選びました" in first[1]
        assert [one.utterance for one in summarizing.read[0]] == [
            "図書館で小説を三冊借りました",
            "やっと通った",
            "トマトの苗を植えました",
            "トマトの苗に水をやりました",
        ]
        assert second[0] == 0 and "1 件を読みましたが、残すものはありませんでした" in second[1]
        assert len(summarizing.read) == 1
        assert third[0] == 0 and "新しい記憶がありません" in third[1]
        memories = _opened(home)
        latest = memories.latest_dream("sora")
        memories.close()
        assert latest is not None and latest.count == 1 and latest.kept == 0


class TestST057002:
    """要点の層と記録と原文と、なぞり。"""

    def test_ST_057_002_要点が残り選んだ往復だけなぞられ失敗では何も積まれない(
        self, tmp_path: Path
    ) -> None:
        home = _home(tmp_path / "home")
        _spoken(
            home,
            [("やっと通った", Moved(0.5, "ほっとした")), ("洗濯物がよく乾きました", None)],
            AT,
        )

        done = _dream(home, _Summarizing(("テストは原因がタイポだった", "三時間かかった")))
        memories = _opened(home)
        dream = memories.latest_dream("sora")
        assert dream is not None and dream.id is not None
        gists = memories.gists_of_dream(dream.id)
        kept_id = memories.episodes_after("sora", None)[0].id
        other_id = memories.episodes_after("sora", None)[1].id
        retrieved = (memories.retrieval(kept_id).count, memories.retrieval(other_id).count)
        memories.close()

        _spoken(home, [("次の課題です", Moved(0.4, "やる気"))], AT + timedelta(hours=2))
        failed = _dream(home, _Failing())
        memories = _opened(home)
        after = memories.latest_dream("sora")
        count = memories.count_episodes("sora")
        failed_retrieved = memories.retrieval(memories.episodes_after("sora", None)[2].id).count
        memories.close()

        assert done[0] == 0
        assert "要点: 2 件を残しました" in done[1] and "  - テストは原因がタイポだった" in done[1]
        assert "気づき: 無し" in done[1] and "選んだ 1 件になぞった記録を残しました" in done[1]
        assert [one.text for one in gists] == ["テストは原因がタイポだった", "三時間かかった"]
        assert all(one.sources == (kept_id,) for one in gists)
        assert dream.count == 2 and dream.kept == 1 and dream.noticing is None
        assert retrieved == (1, 0)
        assert failed[0] == 1 and "夢を見られません" in failed[2]
        assert after == dream and count == 3 and failed_retrieved == 0

    def test_ST_057_002_設定が無ければ理由で終わる(self, tmp_path: Path) -> None:
        code, out, err = _dream(tmp_path / "empty", _Summarizing())

        assert code == 1 and out == "" and err.strip() != ""


class TestST057003:
    """要点が前置きと state に出る。"""

    def test_ST_057_003_要点が思い出しに添い状態にも出る(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        monkeypatch.setenv("YADORI_HOME", str(home))
        _spoken(home, [("やっと通った", Moved(0.5, "ほっとした"))], AT)
        before = self._state()
        _ = _dream(home, _Summarizing(("テストは原因がタイポだった",), "急ぐと見落とす"))

        settings = SettingsFile(home).read()
        memories = SqliteMemories(settings.memories_path)
        call = _Answering()
        _ = Turn(
            Conversation(memories, PAIRS, lambda: AT + timedelta(days=1)), ClaudeCodeVoice(call)
        ).respond_to("sora", "おはよう")
        memories.close()
        after = self._state()
        before_dream = self._state(at=(AT - timedelta(hours=1)).isoformat())

        assert "夢はまだありません" in before
        assert "残した要点です。\n- テストは原因がタイポだった" in call.prefaces[0]
        assert "そのとき気づいたこと: 急ぐと見落とす" in call.prefaces[0]
        assert (
            "最近の夢:" in after
            and "1 件を読み 1 件を選び 要点 1 件  気づき: 急ぐと見落とす" in after
        )
        # 夢より前の時点では、その夢は無かったものとして扱う。
        assert "夢は（その時点では）まだありません" in before_dream

    def _state(self, at: str | None = None) -> str:
        out = io.StringIO()
        real = sys.stdout
        sys.stdout = out
        try:
            _ = Entry(["yadori", "state"] + ([] if at is None else ["--at", at])).run()
        finally:
            sys.stdout = real
        return out.getvalue()

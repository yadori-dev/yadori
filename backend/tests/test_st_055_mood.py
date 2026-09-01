"""INC-055 のシステムテスト。気持ちが会話で動き、時間で薄れ、応対に渡る。

架空の会話で書く。声は差し替え、AIモデルを呼ばない。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from tests.test_st_018_measure import EVAL
from yadori.adapter.embedding import CharacterPairs, Weighing
from yadori.adapter.evaluation import EvalFile
from yadori.adapter.place import Terminal
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.conversation import Spoken
from yadori.domain.memory import Dweller, HowToRecall, Memories, Mood, Moved, Recollection
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn
from yadori.usecase.evaluation import Measuring
from yadori.usecase.evaluation.measure import MEASURED

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")


@final
class _Clock:
    """止めたり進めたりできる時計。"""

    def __init__(self) -> None:
        self.at: datetime = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@final
class _Moving:
    """決めた動きを順に返す声。決めた分が尽きれば動きなし。前置きの気持ちも覚える。"""

    def __init__(self, moves: list[Moved]) -> None:
        self._moves: list[Moved] = list(moves)
        self.moods: list[Mood] = []

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        self.moods.append(recollection.state.mood)
        moved = self._moves.pop(0) if self._moves else Moved.unmoved()
        return Spoken(f"{utterance} ですね", moved)


def _home(tmp_path: Path) -> Path:
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    return tmp_path


class TestST055001:
    """端末で動きが見え、積まれる。"""

    def test_ST_055_001_返事の後に動きといまの値が出て動きが積まれる(self, tmp_path: Path) -> None:
        settings = SettingsFile(_home(tmp_path)).read()
        memories = SqliteMemories(settings.memories_path)
        Startup(tmp_path).settle(memories, settings)
        clock = _Clock()
        voice = _Moving([Moved(-0.3, "一緒に困っている"), Moved(0.5, "ほっとした")])
        written = io.StringIO()

        Terminal(
            Turn(Conversation(memories, CharacterPairs(), clock), voice),
            settings.dweller,
            reading=io.StringIO("三時間やってもテストが通らない\nやっと通った\nありがとう\n\n"),
            writing=written,
        ).listen()

        lines = written.getvalue().splitlines()
        assert "（気持ち: -0.3 一緒に困っている → いま -0.30）" in lines
        assert "（気持ち: +0.5 ほっとした → いま +0.20）" in lines
        assert "（気持ち: +0.0 動きなし → いま +0.20）" in lines
        # 返事に動きの一行は混ざらない。
        assert any(line.endswith("そら: ありがとう ですね") for line in lines)
        shifts = memories.shifts("sora")
        assert [round(one.delta, 1) for one in shifts] == [-0.3, 0.5, 0.0]
        assert shifts[2].cause == "動きなし"
        assert all(one.episode_id is not None for one in shifts)
        memories.close()


class TestST055002:
    """薄れ方が計算で決まる。"""

    def _conversation(self, memories: Memories, clock: _Clock) -> Conversation:
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        return Conversation(memories, CharacterPairs(), clock)

    def test_ST_055_002_半減期ごとに半分になり同じ入力で同じ値(self) -> None:
        clock = _Clock()
        conversation = self._conversation(InMemoryMemories(), clock)
        _ = conversation.remember(SORA.id, "やっと通った", "よかった", Moved(0.5, "ほっとした"))
        started = clock.at

        values: list[float] = []
        for hours in (0, 6, 12, 0):
            clock.at = started + timedelta(hours=hours)
            values.append(conversation.state(SORA.id).mood.value)

        assert [round(value, 3) for value in values] == [0.5, 0.25, 0.125, 0.5]
        # 思い出したことに添う気持ちも、その時点の値である。
        clock.at = started + timedelta(hours=6)
        assert round(conversation.recall(SORA.id, "どう？").state.mood.value, 3) == 0.25


class TestST055003:
    """前置きに気持ちが渡り、測る側は動かさない。"""

    def test_ST_055_003_声に今の気持ちが渡り測る手順は動きを積まない(self, tmp_path: Path) -> None:
        clock = _Clock()
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        voice = _Moving([Moved(-0.4, "つらい")])
        turn = Turn(Conversation(memories, CharacterPairs(), clock), voice)

        _ = turn.respond_to(SORA.id, "テストが通らない")
        _ = turn.respond_to(SORA.id, "まだ通らない")

        assert [round(mood.value, 2) for mood in voice.moods] == [0.0, -0.4]
        assert voice.moods[1].described == "沈んでいる"

        created: list[InMemoryMemories] = []

        def fresh() -> InMemoryMemories:
            created.append(InMemoryMemories())
            return created[-1]

        path = tmp_path / "recall.toml"
        _ = path.write_text(EVAL, encoding="utf-8")
        _ = Measuring(EvalFile(path).read(), fresh, Weighing(CharacterPairs())).at(
            HowToRecall(6, 5, 0.21)
        )

        assert created and all(kept.shifts(MEASURED.id) == () for kept in created)

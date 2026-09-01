"""INC-056 のシステムテスト。性格が同じ動きからゆっくり変わり、時系列と過去の時点を読める。

架空の会話で書く。声は差し替え、AIモデルを呼ばない。
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.adapter.voice import ClaudeCodeVoice
from yadori.domain.conversation import Spoken
from yadori.domain.memory import Character, Dweller, Identity, Mood, Moved, Recollection, State
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
AT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@final
class _Clock:
    def __init__(self) -> None:
        self.at: datetime = AT

    def __call__(self) -> datetime:
        return self.at


@final
class _Answering:
    """道具の口の代わり。決めた文を返し、渡された前置きを覚える。"""

    def __init__(self, answered: str) -> None:
        self._answered: str = answered
        self.prefaces: list[str] = []

    def ask(self, preface: str, spoken: str) -> str:
        del spoken
        self.prefaces.append(preface)
        return self._answered


@final
class _Moving:
    def __init__(self, moves: list[Moved]) -> None:
        self._moves: list[Moved] = list(moves)
        self.states: list[State] = []

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        self.states.append(recollection.state)
        return Spoken(f"{utterance} ですね", self._moves.pop(0) if self._moves else Moved.unmoved())


def _home(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n', encoding="utf-8"
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    return tmp_path


def _state(
    monkeypatch: pytest.MonkeyPatch, home: Path, at: str | None = None
) -> tuple[int, str, str]:
    monkeypatch.setenv("YADORI_HOME", str(home))
    argv = ["yadori", "state"] + ([] if at is None else ["--at", at])
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = Entry(argv).run()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return code, out.getvalue(), err.getvalue()


class TestST056001:
    """性格がゆっくり動き、前置きに渡る。"""

    def test_ST_056_001_性格は十分の一で効き九十日で半分になる(self) -> None:
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        clock = _Clock()
        conversation = Conversation(memories, CharacterPairs(), clock)
        _ = conversation.remember(SORA.id, "やっと通った", "よかった", Moved(0.5, "ほっとした"))

        seen: list[tuple[float, float]] = []
        for later in (timedelta(0), timedelta(hours=6), timedelta(days=90)):
            clock.at = AT + later
            state = conversation.state(SORA.id)
            seen.append((round(state.mood.value, 3), round(state.character.value, 4)))

        assert seen[0] == (0.5, 0.05)
        assert seen[1][0] == 0.25 and 0.049 < seen[1][1] <= 0.05
        assert seen[2][0] < 0.001 and seen[2][1] == 0.025

    def test_ST_056_001_前置きに性格が渡る(self) -> None:
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        voice = _Moving([Moved(0.5, "ほっとした")])
        turn = Turn(Conversation(memories, CharacterPairs(), _Clock()), voice)

        _ = turn.respond_to(SORA.id, "やっと通った")
        _ = turn.respond_to(SORA.id, "次も頑張る")

        assert round(voice.states[1].character.value, 3) == 0.05
        assert voice.states[1].character.described == "落ち着いている"

    def test_ST_056_001_声の前置きに性格の値と言葉が入る(self) -> None:
        call = _Answering("はい。")
        recollection = Recollection(
            Identity(1, "わたしはそらです。"), (), (), State(Mood(0.0), Character(0.05)), None
        )

        _ = ClaudeCodeVoice(call).speak(recollection, "おはよう")

        assert "長い目で見た性格の傾向は「落ち着いている」（+0.05）" in call.prefaces[0]


class TestST056002:
    """時系列と過去の時点を読める。"""

    def _spoken_home(self, tmp_path: Path) -> tuple[Path, _Clock]:
        home = _home(tmp_path / "home")
        settings = SettingsFile(home).read()
        memories = SqliteMemories(settings.memories_path)
        Startup(home).settle(memories, settings)
        clock = _Clock()
        turn = Turn(
            Conversation(memories, CharacterPairs(), clock),
            _Moving([Moved(-0.2, "心配"), Moved(0.3, "ほっとした"), Moved(0.1, "うれしい")]),
        )
        for minutes, said in ((0, "テストが通らない"), (5, "やっと通った"), (10, "次も頑張る")):
            clock.at = AT + timedelta(minutes=minutes)
            _ = turn.respond_to("sora", said)
        memories.close()
        return home, clock

    def test_ST_056_002_動きが新しい順に発話付きで並び時点で切れる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home, _ = self._spoken_home(tmp_path)

        code, out, _ = _state(monkeypatch, home)
        code_at, out_at, _ = _state(monkeypatch, home, (AT + timedelta(minutes=6)).isoformat())

        assert code == 0 and code_at == 0
        lines = out.splitlines()
        assert lines[0].startswith("気持ち: ") and "半減期 6 時間" in lines[0]
        assert lines[1].startswith("性格: ") and "半減期 90 日" in lines[1]
        assert lines[2] == "夢はまだありません"
        assert lines[3] == "動き（新しい順）:"
        assert [line.split()[2] for line in lines[4:7]] == ["+0.1", "+0.3", "-0.2"]
        assert "「次も頑張る」" in lines[4] and "「テストが通らない」" in lines[6]
        at_lines = out_at.splitlines()
        assert "時点" in at_lines[0] and "時点" in at_lines[1]
        assert len(at_lines) == 6 and "「次も頑張る」" not in out_at
        assert at_lines[0].startswith("気持ち: +0.10")

    def test_ST_056_002_動きが無ければ0とその旨(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _home(tmp_path / "home")
        settings = SettingsFile(home).read()
        memories = SqliteMemories(settings.memories_path)
        Startup(home).settle(memories, settings)
        memories.close()

        code, out, _ = _state(monkeypatch, home)

        assert code == 0
        assert out.splitlines()[0].startswith("気持ち: +0.00")
        assert "動きはまだありません" in out

    def test_ST_056_002_設定が無ければ理由で終わる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code, out, err = _state(monkeypatch, tmp_path / "empty")

        assert code == 1 and out == "" and err.strip() != ""

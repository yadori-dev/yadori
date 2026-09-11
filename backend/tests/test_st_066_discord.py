"""INC-066 のシステムテスト。

Discord から持ち主が話しかけると宿り自身の言葉で返り、端末と行き来しても同じ相手として続く。
架空の会話で書く。実物の Discord へは繋がず、受け取る繋ぎを差し替える。
"""

from __future__ import annotations

import asyncio
import io
import sys
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import final

import pytest

from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.place import Answering, DiscordPlace, Heard, Terminal
from yadori.adapter.store import SqliteMemories
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import Moved, Recollection
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

AT = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
PAIRS = CharacterPairs()
OWNER = 111
SOMEONE = 222


@final
class _Thinking:
    """考えている印の代わり。立てられた回数を数える。"""

    def __init__(self) -> None:
        self.shown: int = 0

    def __call__(self) -> AbstractAsyncContextManager[object]:
        return self

    async def __aenter__(self) -> None:
        self.shown += 1

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        trouble: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del kind, trouble, traceback


@final
class _Gateway:
    """受け取る繋ぎの代わり。決めた言葉を届け、送られた文と挨拶を覚える。"""

    def __init__(self, heard: list[Heard]) -> None:
        self._heard: list[Heard] = heard
        self.sent: list[tuple[str, ...]] = []
        self.greeted: str = ""
        self.thinking: _Thinking = _Thinking()

    def listen(self, answering: Answering, greeting: str) -> None:
        self.greeted = greeting
        for one in self._heard:
            letters = asyncio.run(answering(one, self.thinking))
            if letters:
                self.sent.append(letters)


@final
class _Fixed:
    """決めた返事と動きを返す声。渡された思い出しも覚える。"""

    def __init__(self, reply: str = "そうなんですね", moved: float = 0.4) -> None:
        self._reply: str = reply
        self._moved: float = moved
        self.recollections: list[Recollection] = []

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        self.recollections.append(recollection)
        return Spoken(f"{self._reply}（{utterance}）", Moved(self._moved, "うれしい"))


@final
class _Silent:
    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        del recollection, utterance
        raise CannotSpeak("応対の道具が /home/架空/仕事場 で応えませんでした")


def _direct(text: str, author_id: int = OWNER) -> Heard:
    return Heard(text=text, author_id=author_id, direct=True, from_myself=False)


def _home(tmp_path: Path, with_discord: bool = True) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n', encoding="utf-8"
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    if with_discord:
        _ = (tmp_path / "discord.toml").write_text(
            f'token = "架空のトークン"\nowner_id = {OWNER}\n', encoding="utf-8"
        )
    return tmp_path


def _opened(home: Path) -> SqliteMemories:
    settings = SettingsFile(home).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(home).settle(memories, settings)
    return memories


class TestST066001:
    """誰の言葉に答えるか。"""

    @pytest.mark.parametrize(
        ("heard", "answered"),
        [
            (_direct("トマトを植えました"), True),
            (_direct("こんにちは", author_id=SOMEONE), False),
            (Heard("部屋での言葉", OWNER, direct=False, from_myself=False), False),
            (Heard("自分の言葉", OWNER, direct=True, from_myself=True), False),
            (_direct("   "), False),
        ],
    )
    def test_ST_066_001_持ち主の直接の会話にだけ答える(
        self, tmp_path: Path, heard: Heard, answered: bool
    ) -> None:
        home = _home(tmp_path / "home")
        memories = _opened(home)
        voice = _Fixed()
        gateway = _Gateway([heard])

        dweller = SettingsFile(home).read().dweller
        DiscordPlace(
            Turn(Conversation(memories, PAIRS, lambda: AT), voice), dweller, OWNER, gateway
        ).listen()
        kept = memories.count_episodes("sora")
        shifts = len(memories.shifts("sora"))
        memories.close()

        assert bool(gateway.sent) is answered
        assert (kept, shifts) == ((1, 1) if answered else (0, 0))
        # 考えている印は、答えると決めた言葉にだけ立つ。答えない言葉では立てない
        # （サーバーの部屋で「入力中」だけが出ることを防ぐ）。
        assert gateway.thinking.shown == (1 if answered else 0)


class TestST066002:
    """端末と行き来しても同じ相手。"""

    def test_ST_066_002_直近と気持ちが端末とDiscordをまたいで続く(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        memories = _opened(home)
        dweller = SettingsFile(home).read().dweller
        voice = _Fixed()
        turn = Turn(Conversation(memories, PAIRS, lambda: AT), voice)

        DiscordPlace(turn, dweller, OWNER, _Gateway([_direct("トマトを植えました")])).listen()
        Terminal(
            turn, dweller, reading=io.StringIO("水やりはどうですか\n\n"), writing=io.StringIO()
        ).listen()
        last = _Gateway([_direct("そのあとどうなりました")])
        DiscordPlace(turn, dweller, OWNER, last).listen()

        # 端末の応対には Discord の往復が、Discord の応対には端末の往復が直近として渡る。
        spoken = [[episode.utterance for episode in one.recent] for one in voice.recollections]
        assert spoken[0] == []
        assert spoken[1] == ["トマトを植えました"]
        assert spoken[2] == ["トマトを植えました", "水やりはどうですか"]
        assert len(memories.shifts("sora")) == 3
        assert memories.count_episodes("sora") == 3
        # 気持ちの印の行は Discord へ出さない（声が切り出した後の文だけを送る）。
        assert last.sent == [("そうなんですね（そのあとどうなりました）",)]
        # 名乗りは場所が組む。Discord の繋ぎは何と言うかを持たない。
        assert "そら" in last.greeted
        memories.close()


class TestST066003:
    """トークンと失敗の伝え方。"""

    @pytest.mark.parametrize(
        "written",
        [
            None,
            "owner_id = 111\n",
            'token = "架空のトークン"\n',
            'token = "架空のトークン"\nowner_id = "百十一"\n',
            'token = "架空のトークン\nowner_id = 111\n',
        ],
    )
    def test_ST_066_003_トークンが無いか形が違えば理由が返る(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: str | None
    ) -> None:
        home = _home(tmp_path / "home", with_discord=False)
        if written is not None:
            _ = (home / "discord.toml").write_text(written, encoding="utf-8")
        monkeypatch.setenv("YADORI_HOME", str(home))
        errors = io.StringIO()
        real = sys.stderr
        sys.stderr = errors
        try:
            code = Entry(["yadori", "discord"]).run()
        finally:
            sys.stderr = real

        assert code == 1
        assert "discord.toml" in errors.getvalue()
        assert "token" in errors.getvalue() and "owner_id" in errors.getvalue()
        assert "架空のトークン" not in errors.getvalue()

    def test_ST_066_003_応対を作れなければ理由は手元にだけ残る(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        memories = _opened(home)
        gateway = _Gateway([_direct("トマトを植えました")])
        here = io.StringIO()

        DiscordPlace(
            Turn(Conversation(memories, PAIRS, lambda: AT), _Silent()),
            SettingsFile(home).read().dweller,
            OWNER,
            gateway,
            writing=here,
        ).listen()
        kept = memories.count_episodes("sora")
        memories.close()

        assert len(gateway.sent) == 1
        assert "いま応対を作れません" in gateway.sent[0][0]
        # 理由には手元の道具の出力がそのまま入る。Discord へは送らず、手元にだけ書く。
        assert "/home/架空/仕事場" not in gateway.sent[0][0]
        assert "/home/架空/仕事場" in here.getvalue()
        assert kept == 0

    def test_ST_066_003_長い返事は分けて送られ合わせると元になる(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home")
        memories = _opened(home)
        long_reply = "\n".join(["あ" * 500] * 6)
        gateway = _Gateway([_direct("長い話を聞かせて")])

        DiscordPlace(
            Turn(Conversation(memories, PAIRS, lambda: AT), _Fixed(reply=long_reply)),
            SettingsFile(home).read().dweller,
            OWNER,
            gateway,
        ).listen()
        memories.close()

        letters = gateway.sent[0]
        assert len(letters) > 1
        assert all(len(one) <= 2000 for one in letters)
        assert "".join(letters).replace("\n", "") == (
            f"{long_reply}（長い話を聞かせて）".replace("\n", "")
        )

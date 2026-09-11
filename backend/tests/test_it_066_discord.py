"""INC-066 の結合テスト。

場所が答えるかどうかを決めること、起動が場所を受け取ること、トークンの読み方と伏せ方、
長い返事の分け方と失敗の返し方、実物の discord.py への配線。
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import final

import pytest

from tests.sora import fixed
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.place import (
    Answering,
    CannotConnect,
    DiscordGateway,
    DiscordPlace,
    Heard,
    Place,
    Thinking,
)
from yadori.adapter.store import InMemoryMemories, SqliteMemories
from yadori.domain.conversation import CannotSpeak, Spoken
from yadori.domain.memory import (
    Dweller,
    EmbeddingsUnavailable,
    Moved,
    Provenance,
    Recollection,
    Vector,
)
from yadori.infrastructure.settings import DiscordSettings, NotSettled, Settings, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")
AT = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
PAIRS = CharacterPairs()
OWNER = 111


@final
class _Quiet:
    """何も見せない考えている印。順に扱われるかだけを見たいときに使う。"""

    def __call__(self) -> AbstractAsyncContextManager[object]:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        trouble: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del kind, trouble, traceback


@final
class _Showing:
    """考えている印の代わり。出入りを並びへ書き残す。"""

    def __init__(self, marks: list[str]) -> None:
        self._marks: list[str] = marks

    def __call__(self) -> AbstractAsyncContextManager[object]:
        return self

    async def __aenter__(self) -> None:
        self._marks.append("入")

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        trouble: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del kind, trouble, traceback
        self._marks.append("出")


@final
class _Gateway:
    def __init__(self, heard: list[Heard]) -> None:
        self._heard: list[Heard] = heard
        self.sent: list[tuple[str, ...]] = []

    def listen(self, answering: Answering, greeting: str) -> None:
        del greeting
        for one in self._heard:
            letters = asyncio.run(answering(one, _Quiet()))
            if letters:
                self.sent.append(letters)


@final
class _Together:
    """届いた言葉を一つの流れの中で同時に投げる繋ぎ。順に扱われるかを見る。"""

    def __init__(self, heard: list[Heard]) -> None:
        self._heard: list[Heard] = heard
        self.sent: list[tuple[str, ...]] = []

    def listen(self, answering: Answering, greeting: str) -> None:
        del greeting
        self.sent = asyncio.run(self._all_at_once(answering))

    async def _all_at_once(self, answering: Answering) -> list[tuple[str, ...]]:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(answering(one, _Quiet())) for one in self._heard]
        return [task.result() for task in tasks]


@final
class _Fixed:
    def __init__(self, reply: str = "はい") -> None:
        self._reply: str = reply

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        del recollection, utterance
        return Spoken(self._reply, Moved.unmoved())


@final
class _Slow:
    """応対に時間がかかる声。入ったところと出たところを並びへ書き残す。"""

    def __init__(self) -> None:
        self.order: list[str] = []

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        del recollection
        self.order.append(f"入{utterance}")
        time.sleep(0.02)
        self.order.append(f"出{utterance}")
        return Spoken(f"はい{utterance}", Moved.unmoved())


@final
class _Failing:
    def __init__(self, trouble: Exception) -> None:
        self._trouble: Exception = trouble

    def speak(self, recollection: Recollection, utterance: str) -> Spoken:
        del recollection, utterance
        raise self._trouble


@final
class _Waiting:
    """待つ場所の代わり。渡された一往復の手順と設定を覚える。"""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.settings: list[Settings] = []

    def listen(self) -> None:
        pass


@final
class _Unreachable:
    """繋げない場所の代わり。"""

    def listen(self) -> None:
        raise CannotConnect("Discord へ繋げません: 架空の理由")


@final
class _Fixed_Embeddings:
    """AIモデルを読み込まない埋め込み。起動の組み立てを確かめるために差し込む。"""

    @property
    def provenance(self) -> Provenance:
        return Provenance(None, "fixed", "v1")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_remember(self, text: str) -> Vector:
        return PAIRS.to_remember(text)

    def to_recall(self, text: str) -> Vector:
        return PAIRS.to_recall(text)


@final
@dataclass(frozen=True)
class _Author:
    id: int


@final
@dataclass(frozen=True)
class _Message:
    """届いた言葉の代わり。discord.py が渡すもののうち、使う三つだけを持つ。"""

    content: str
    author: _Author
    channel: object


@final
class _Watched:
    """`Client.run` を差し替えて、組み上がった client と渡された引数を捕まえる。"""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.client: object = None
        self.token: str = ""
        self.given: dict[str, object] = {}
        self._refuse: Exception | None = refuse

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import discord

        def fake_run(client: object, token: str, **rest: object) -> None:
            self.client = client
            self.token = token
            self.given = rest
            if self._refuse is not None:
                raise self._refuse

        monkeypatch.setattr(discord.Client, "run", fake_run)


def _turn(memories: InMemoryMemories, voice: object) -> Turn:
    memories.settle(SORA)
    _ = memories.write_identity(SORA.id, "わたしはそらです。")
    return Turn(Conversation(memories, PAIRS, lambda: AT), voice)  # pyright: ignore[reportArgumentType]


def _home(tmp_path: Path, written: str | None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n', encoding="utf-8"
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    if written is not None:
        _ = (tmp_path / "discord.toml").write_text(written, encoding="utf-8")
    return tmp_path


class TestIT066001:
    """場所が答えるかどうかを決め、繋ぎは判断しない。"""

    def test_IT_066_001_持ち主の直接の会話だけに返事が返る(self) -> None:
        memories = InMemoryMemories()
        heard = [
            Heard("持ち主から", OWNER, direct=True, from_myself=False),
            Heard("他の人から", 222, direct=True, from_myself=False),
            Heard("部屋で", OWNER, direct=False, from_myself=False),
            Heard("自分の言葉", OWNER, direct=True, from_myself=True),
            Heard("  ", OWNER, direct=True, from_myself=False),
        ]
        gateway = _Gateway(heard)

        DiscordPlace(_turn(memories, _Fixed()), SORA, OWNER, gateway).listen()

        assert gateway.sent == [("はい",)]
        assert memories.count_episodes(SORA.id) == 1


class TestIT066002:
    """起動が場所を受け取り、同じ手順を通る。"""

    def test_IT_066_002_渡した場所で待ち既定は端末(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        home = _home(tmp_path / "home", None)
        waiting = _Waiting()

        def where(turn: Turn, settings: Settings) -> Place:
            waiting.turns.append(turn)
            waiting.settings.append(settings)
            return waiting

        given = Startup(home, default=fixed(_Fixed_Embeddings())).run(where)
        # 何も渡さなければ端末で待つ。端末は標準入力を読むので、空行で終える。
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
        default = Startup(home, default=fixed(_Fixed_Embeddings())).run()
        written = capsys.readouterr().out

        assert given == 0 and default == 0
        assert len(waiting.turns) == 1
        # 渡した場所へ届く前に、宿りは住まわされ名乗りが書かれている（端末と同じ手順）。
        settled = SqliteMemories(waiting.settings[0].memories_path)
        current = settled.current_identity("sora")
        settled.close()
        assert current is not None and current.text == "わたしはそらです。"
        # 既定の場所は端末で、端末の呼びかけが出る。
        assert "そら が居ます" in written

    def test_IT_066_002_繋げなければ理由を書いて終わる(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home", None)

        code = Startup(home, default=fixed(_Fixed_Embeddings())).run(
            lambda turn, settings: _Unreachable()
        )

        assert code == 1


class TestIT066003:
    """トークンの読み方と伏せ方。"""

    @pytest.mark.parametrize(
        ("written", "wrong"),
        [
            (None, "がありません"),
            ("owner_id = 111\n", "token がありません"),
            ('token = "秘密"\n', "owner_id が数ではありません"),
            ('token = "秘密"\nowner_id = "百十一"\n', "owner_id が数ではありません"),
            ('token = ""\nowner_id = 111\n', "token がありません"),
            ('token = "秘密\nowner_id = 111\n', "書き方が壊れています"),
        ],
    )
    def test_IT_066_003_足りなければ書き方を添えて断る(
        self, tmp_path: Path, written: str | None, wrong: str
    ) -> None:
        home = _home(tmp_path / "home", written)

        with pytest.raises(NotSettled) as refused:
            _ = SettingsFile(home).discord()

        assert wrong in str(refused.value)
        assert "token =" in str(refused.value) and "owner_id =" in str(refused.value)
        assert "秘密" not in str(refused.value)

    def test_IT_066_003_正しければ読め人が読む形にも出ない(self, tmp_path: Path) -> None:
        home = _home(tmp_path / "home", 'token = "秘密"\nowner_id = 111\n')

        settings = SettingsFile(home).discord()

        assert settings == DiscordSettings(token="秘密", owner_id=111)
        assert "秘密" not in repr(settings) and "111" in repr(settings)


class TestIT066004:
    """長い返事の分け方、順に扱うこと、失敗の返し方。"""

    @pytest.mark.parametrize(
        ("reply", "letters"),
        [
            ("あ" * 2000, 1),
            ("\n".join(["あ" * 1000] * 3), 3),
            ("あ" * 4500, 3),
        ],
    )
    def test_IT_066_004_受け取れる長さに分ける(self, reply: str, letters: int) -> None:
        gateway = _Gateway([Heard("長い話を", OWNER, direct=True, from_myself=False)])

        DiscordPlace(_turn(InMemoryMemories(), _Fixed(reply)), SORA, OWNER, gateway).listen()

        sent = gateway.sent[0]
        assert len(sent) == letters
        assert all(0 < len(one) <= 2000 for one in sent)
        assert "".join(sent).replace("\n", "") == reply.replace("\n", "")

    def test_IT_066_004_改行があればそこで切れる(self) -> None:
        reply = "あ" * 1500 + "\n" + "い" * 1000
        gateway = _Gateway([Heard("長い話を", OWNER, direct=True, from_myself=False)])

        DiscordPlace(_turn(InMemoryMemories(), _Fixed(reply)), SORA, OWNER, gateway).listen()

        assert gateway.sent[0] == ("あ" * 1500, "い" * 1000)

    def test_IT_066_004_重なって届いても一往復ずつ順に扱う(self) -> None:
        memories = InMemoryMemories()
        voice = _Slow()
        gateway = _Together(
            [Heard(one, OWNER, direct=True, from_myself=False) for one in ("あ", "い", "う")]
        )

        DiscordPlace(_turn(memories, voice), SORA, OWNER, gateway).listen()

        # 入ったら必ず出るまで次が入らない。重なると 入入入出出出 の並びになる。
        assert [one[0] for one in voice.order] == list("入出入出入出")
        assert [voice.order[i][1:] for i in (0, 2, 4)] == [voice.order[i][1:] for i in (1, 3, 5)]
        assert len(gateway.sent) == 3
        assert memories.count_episodes(SORA.id) == 3

    @pytest.mark.parametrize(
        "trouble",
        [
            CannotSpeak("応対の道具が /home/架空/仕事場 で応えませんでした"),
            EmbeddingsUnavailable("`uv sync` してください"),
        ],
    )
    def test_IT_066_004_作れない失敗は理由を手元へ書き覚えない(self, trouble: Exception) -> None:
        memories = InMemoryMemories()
        gateway = _Gateway([Heard("トマトを植えました", OWNER, direct=True, from_myself=False)])
        here = io.StringIO()

        DiscordPlace(
            _turn(memories, _Failing(trouble)), SORA, OWNER, gateway, writing=here
        ).listen()

        assert len(gateway.sent) == 1 and len(gateway.sent[0]) == 1
        assert "いま応対を作れません" in gateway.sent[0][0]
        # 理由には手元の場所や道具の内部の文言が混ざりうる。Discord へは送らない。
        assert str(trouble) not in gateway.sent[0][0]
        assert str(trouble) in here.getvalue()
        assert memories.count_episodes(SORA.id) == 0


class TestIT066005:
    """実物の discord.py への配線。繋ぐところだけ差し替える。"""

    def test_IT_066_005_直接の会話だけを受け取り答えるときだけ印を出す(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import discord

        sent: list[str] = []
        marks: list[str] = []

        async def fake_send(channel: object, content: str = "", **rest: object) -> None:
            del channel, rest
            sent.append(content)

        def fake_typing(channel: object) -> _Showing:
            del channel
            return _Showing(marks)

        monkeypatch.setattr(discord.abc.Messageable, "send", fake_send)
        monkeypatch.setattr(discord.abc.Messageable, "typing", fake_typing)
        watched = _Watched()
        watched.install(monkeypatch)
        memories = InMemoryMemories()

        DiscordPlace(
            _turn(memories, _Fixed()), SORA, OWNER, DiscordGateway("架空のトークン")
        ).listen()

        client = watched.client
        assert isinstance(client, discord.Client)
        # 受け取るのは直接の会話だけ。サーバーの部屋の言葉はそもそも届かない（ADR-019）。
        assert client.intents.dm_messages is True
        assert client.intents.guild_messages is False
        # 直接の会話の中身は、開発者向けの画面の MESSAGE CONTENT INTENT なしで読める。
        assert client.intents.message_content is False
        assert watched.token == "架空のトークン"
        assert watched.given["log_level"] == logging.WARNING

        # `@client.event` は関数名で登録する。登録された合図は instance の中身から取る。
        on_ready = client.__dict__["on_ready"]  # pyright: ignore[reportAny]
        _ = asyncio.run(on_ready())  # pyright: ignore[reportAny]
        assert "そら が Discord に居ます" in capsys.readouterr().out

        on_message = client.__dict__["on_message"]  # pyright: ignore[reportAny]
        # 部屋の言葉が万一届いても、送らず、入力中も出さない。
        room = _Message("部屋での言葉", _Author(OWNER), object.__new__(discord.TextChannel))
        _ = asyncio.run(on_message(room))  # pyright: ignore[reportAny]
        assert (sent, marks) == ([], [])

        direct = _Message("トマトを植えました", _Author(OWNER), object.__new__(discord.DMChannel))
        _ = asyncio.run(on_message(direct))  # pyright: ignore[reportAny]
        assert sent == ["はい"]
        assert marks == ["入", "出"]
        assert memories.count_episodes(SORA.id) == 1

    def test_IT_066_005_送れなくても止まらず理由が手元に残る(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import discord

        async def failing_send(channel: object, content: str = "", **rest: object) -> None:
            del channel, content, rest
            raise RuntimeError("送れませんでした")

        def quiet_typing(channel: object) -> _Quiet:
            del channel
            return _Quiet()

        monkeypatch.setattr(discord.abc.Messageable, "send", failing_send)
        monkeypatch.setattr(discord.abc.Messageable, "typing", quiet_typing)
        watched = _Watched()
        watched.install(monkeypatch)

        DiscordPlace(
            _turn(InMemoryMemories(), _Fixed()), SORA, OWNER, DiscordGateway("架空のトークン")
        ).listen()
        client = watched.client
        assert isinstance(client, discord.Client)
        on_message = client.__dict__["on_message"]  # pyright: ignore[reportAny]
        direct = _Message("トマトを植えました", _Author(OWNER), object.__new__(discord.DMChannel))
        _ = asyncio.run(on_message(direct))  # pyright: ignore[reportAny]

        # discord.py は自前の記録へ流すだけで外へ出さない。黙って落とさず手元へ書く。
        assert "送れませんでした" in capsys.readouterr().err

    def test_IT_066_005_トークンが違えば繋げない理由が返る(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import discord

        async def never(heard: Heard, thinking: Thinking) -> tuple[str, ...]:
            del heard, thinking
            raise AssertionError("繋げていないのに答えようとした")

        watched = _Watched(refuse=discord.LoginFailure("Improper token has been passed."))
        watched.install(monkeypatch)

        with pytest.raises(CannotConnect) as refused:
            DiscordGateway("架空のトークン").listen(never, "（そら が Discord に居ます）")

        assert "Discord へ繋げません" in str(refused.value)
        assert "架空のトークン" not in str(refused.value)

"""端末から一往復が通ることを、外の入口から確かめる。

模型を呼ばないため、応対そのものの良し悪しは見ない。見るのは、設定から
組み立て、話しかけ、覚えるまでの道が繋がっていることである。名乗りどおりの
応対かどうかは `ST-006-001` が模型を使って確かめる。
"""

from __future__ import annotations

import io
from pathlib import Path

from tests.sora import Ticking
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.place import Terminal
from yadori.adapter.store import SqliteMemories
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import Recollection
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

NAME_DECLARED = "わたしはそらです。ていねいな言葉で話し、園芸を好みます。"


def _home(tmp_path: Path, *, declared: str = NAME_DECLARED) -> Path:
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (tmp_path / "identity.md").write_text(declared, encoding="utf-8")
    return tmp_path


class _Echoing:
    def speak(self, recollection: Recollection, utterance: str) -> str:
        return f"（覚えている件数 {len(recollection.found)}）{utterance} ですね"


class _Silent:
    def speak(self, recollection: Recollection, utterance: str) -> str:
        del recollection, utterance
        raise CannotSpeak("模型が応えない")


def _run(home: Path, spoken: str, voice: object) -> tuple[str, SqliteMemories]:
    settings = SettingsFile(home).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(home).settle(memories, settings)
    conversation = Conversation(memories, CharacterPairs(), Ticking())
    written = io.StringIO()
    Terminal(
        Turn(conversation, voice),  # pyright: ignore[reportArgumentType]
        settings.dweller,
        reading=io.StringIO(spoken),
        writing=written,
    ).listen()
    return written.getvalue(), memories


def test_端末から話しかけると応対が返り記憶に残る(tmp_path: Path) -> None:
    written, memories = _run(_home(tmp_path), "トマトを植えました\n\n", _Echoing())

    assert "そら" in written
    assert "トマトを植えました ですね" in written
    assert memories.count_episodes("sora") == 1
    memories.close()


def test_応対を作れないと理由が出て記憶に残らない(tmp_path: Path) -> None:
    written, memories = _run(_home(tmp_path), "トマトを植えました\n\n", _Silent())

    assert "応対できませんでした" in written
    assert memories.count_episodes("sora") == 0
    memories.close()


def test_名乗りを書き直すと新しい版になり以前の版も残る(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _run(home, "\n", _Echoing())[1].close()

    _ = (home / "identity.md").write_text("わたしはそらです。短く話します。", encoding="utf-8")
    _, memories = _run(home, "\n", _Echoing())

    current = memories.current_identity("sora")
    assert current is not None
    assert current.version == 2
    first = memories.identity_at("sora", 1)
    assert first is not None
    assert first.text == NAME_DECLARED
    memories.close()

"""INC-025 のシステムテスト。埋め込みが使えないとき、端末に理由が出ることを確かめる。

AIモデルを呼ばない。使えない埋め込みを差し込み、話す人に何が見えるかだけを見る。
鍵が無く外へ繋がらなくても思い出せること（`ST-025-004` の前半）は、AIモデルを
手元で動かす実装そのものが担うため、契約テストで実物に当てて確かめる。
"""

from __future__ import annotations

import io
from pathlib import Path

from tests.sora import Ticking
from yadori.adapter.place import Terminal
from yadori.adapter.store import SqliteMemories
from yadori.domain.memory import EmbeddingsUnavailable, Provenance, Recollection, Vector
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation, Turn

HOW_TO_INSTALL = "`uv sync` で依存を導入してください"


class _Missing:
    """導入されていない埋め込み。理由に何をすればよいかを含める。"""

    @property
    def provenance(self) -> Provenance:
        return Provenance(ai_model=None, tool="missing", tool_version="v0")

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def of(self, text: str) -> Vector:
        del text
        raise EmbeddingsUnavailable(f"意味を見る埋め込みを使えません。{HOW_TO_INSTALL}。")


class _Echoing:
    def speak(self, recollection: Recollection, utterance: str) -> str:
        return f"（覚えている件数 {len(recollection.found)}）{utterance} ですね"


def _home(tmp_path: Path) -> Path:
    _ = (tmp_path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
    return tmp_path


def test_ST_025_004_埋め込みが使えないと端末に導入の仕方が出て記憶は増えない(
    tmp_path: Path,
) -> None:
    settings = SettingsFile(_home(tmp_path)).read()
    memories = SqliteMemories(settings.memories_path)
    Startup(tmp_path).settle(memories, settings)
    written = io.StringIO()

    Terminal(
        Turn(Conversation(memories, _Missing(), Ticking()), _Echoing()),
        settings.dweller,
        reading=io.StringIO("トマトを植えました\n\n"),
        writing=written,
    ).listen()

    assert "応対できませんでした" in written.getvalue()
    assert HOW_TO_INSTALL in written.getvalue()
    assert memories.count_episodes("sora") == 0
    memories.close()

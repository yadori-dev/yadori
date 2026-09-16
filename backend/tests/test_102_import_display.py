"""起動準備では繰り返す注意を短くし、詳細と保存結果は保つ。"""

from __future__ import annotations

import io
from pathlib import Path

from tests.test_it_091_log_boundaries import answer, log, user
from tests.test_it_091_preparing import settings
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.importing.archive import SqliteArchive
from yadori.infrastructure.importing import Importer, ImportSource


def test_ST_102_004_IT_102_003_未完の大量表示を省いても完成記録は同じ(tmp_path: Path) -> None:
    source = ImportSource(
        "claude",
        log(
            tmp_path / "log.jsonl",
            [
                user("u1", None, "質問"),
                answer("a1", "u1", "返事"),
                *(user(f"pending-{i}", None, "未完の質問") for i in range(100)),
            ],
        ),
    )
    outputs: list[str] = []
    for concise in (False, True):
        home = tmp_path / str(concise)
        settings(home)
        writing = io.StringIO()
        importing = Importer(home, writing, lambda _p, _a: CharacterPairs())
        assert importing.run([source], "a", True, concise=concise) == 0
        assert SqliteArchive(home / "memories.sqlite").existing("a", "claude:fixture:u1")
        outputs.append(writing.getvalue())
    assert "未完" in outputs[0]
    assert outputs[1] == "取り込み完了: 1 件 / 残り: 0 件\n"


def test_ST_102_004_IT_102_003_短い表示でも破損を隠さない(tmp_path: Path) -> None:
    settings(tmp_path)
    broken = tmp_path / "broken.jsonl"
    _ = broken.write_text("{broken}\n")
    writing = io.StringIO()
    importer = Importer(tmp_path, writing, lambda _p, _a: CharacterPairs())
    assert importer.run([ImportSource("claude", broken)], "a", True, concise=True) == 1
    assert "取り込み失敗" in writing.getvalue()
    assert not (tmp_path / "memories.sqlite").exists()

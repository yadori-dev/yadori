"""識別子の対応の検査が、実際に切れた対応を捕まえることを確かめる。

要求分析の上下の対応は人が読んで守るものなので、検査が働かなくなると
静かに崩れる。壊れた形を渡して落ちることを見る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_trace.py"

PROBLEMS = """# 背景と課題

| 課題 | 起きていること |
|---|---|
| `PB-001` 覚えない | 前回を覚えていない。［観測］ |
"""

SCENARIOS = """# 利用者とシナリオ

### SC-001 昨日の続きを話す

話す人が前提を説明せずに話しかける。
"""

DEMANDS = """# 要求

| 要求 | 必要な状態 | 課題 |
|---|---|---|
| `REQ-001` | 前の会話を踏まえた応対を受けられる | `PB-001` |
"""

CRITERIA = """# 受入基準

| 基準 | 判定する状態 | 場面 |
|---|---|---|
| `AC-001` → `REQ-001` | 説明せずに踏まえた応対が返る | `SC-001` |
"""


def build(root: Path, **overrides: str) -> None:
    documents = {
        "01-背景と課題.md": PROBLEMS,
        "02-利用者とシナリオ.md": SCENARIOS,
        "03-要求.md": DEMANDS,
        "04-受入基準.md": CRITERIA,
    }
    documents.update(
        {
            "01-背景と課題.md": overrides.get("problems", PROBLEMS),
            "02-利用者とシナリオ.md": overrides.get("scenarios", SCENARIOS),
            "03-要求.md": overrides.get("demands", DEMANDS),
            "04-受入基準.md": overrides.get("criteria", CRITERIA),
        }
    )
    directory = root / "docs" / "110_requirements"
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in documents.items():
        (directory / name).write_text(body, encoding="utf-8")
    (root / "docs" / "010_decisions").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "210_increments").mkdir(parents=True, exist_ok=True)


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_上下がつながっていれば通る(tmp_path: Path) -> None:
    build(tmp_path)

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_存在しない課題を指す要求は落ちる(tmp_path: Path) -> None:
    build(
        tmp_path,
        demands=DEMANDS.replace("`PB-001`", "`PB-999`"),
    )

    result = run(tmp_path)

    assert result.returncode == 1
    assert "存在しない PB-999" in result.stderr


def test_受入基準の無い要求は落ちる(tmp_path: Path) -> None:
    build(
        tmp_path,
        demands=DEMANDS + "| `REQ-002` | 気持ちが続く | `PB-001` |\n",
    )

    result = run(tmp_path)

    assert result.returncode == 1
    assert "REQ-002 に対応する受入基準が無い" in result.stderr


def test_扱う要求の無い課題は落ちる(tmp_path: Path) -> None:
    build(
        tmp_path,
        problems=PROBLEMS + "| `PB-002` | 思い出さない | 探しに行かない。［観測］ |\n",
    )

    result = run(tmp_path)

    assert result.returncode == 1
    assert "PB-002 を扱う要求が無い" in result.stderr


def test_ADR_の番号が重複していると落ちる(tmp_path: Path) -> None:
    build(tmp_path)
    decisions = tmp_path / "docs" / "010_decisions"
    (decisions / "ADR-001-片方.md").write_text("# ADR-001", encoding="utf-8")
    (decisions / "ADR-001-もう片方.md").write_text("# ADR-001", encoding="utf-8")

    result = run(tmp_path)

    assert result.returncode == 1
    assert "ADR-001 が重複している" in result.stderr


INCREMENT = """# INC-001 例

| 要件 | 振る舞い |
|---|---|
| `SPEC-001` | 話しかけると応対が返る |

| テスト | 対応 | 入口と入力 | 観測する結果 |
|---|---|---|---|
| `ST-001` | `SPEC-001` | 架空の宿りへ話しかける | 応対が返る |
"""


def build_increment(root: Path, body: str) -> None:
    build(root)
    (root / "docs" / "210_increments" / "INC-001.md").write_text(body, encoding="utf-8")


def test_要件とテストが対なら通る(tmp_path: Path) -> None:
    build_increment(tmp_path, INCREMENT)

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_確かめるテストの無い要件は落ちる(tmp_path: Path) -> None:
    build_increment(tmp_path, INCREMENT + "| `SPEC-002` | 名乗りどおりに返る |\n")

    result = run(tmp_path)

    assert result.returncode == 1
    assert "SPEC-002 を確かめる ST が無い" in result.stderr


def test_要件を指さないテストは落ちる(tmp_path: Path) -> None:
    build_increment(tmp_path, INCREMENT + "| `ST-002` | | 何かする | 何か返る |\n")

    result = run(tmp_path)

    assert result.returncode == 1
    assert "ST-002 が対応する SPEC を指していない" in result.stderr


def test_同じ増分に無い要件を指すテストは落ちる(tmp_path: Path) -> None:
    build_increment(tmp_path, INCREMENT + "| `ST-002` | `SPEC-999` | 何かする | 何か返る |\n")

    result = run(tmp_path)

    assert result.returncode == 1
    assert "ST-002 が同じ増分に無い SPEC-999 を指している" in result.stderr


def test_設計と結合テストにも同じ規則が働く(tmp_path: Path) -> None:
    build_increment(
        tmp_path,
        INCREMENT + "\n| 判断 | 内容 |\n|---|---|\n| `AD-001` | 記憶を宿りへ結ぶ |\n",
    )

    result = run(tmp_path)

    assert result.returncode == 1
    assert "AD-001 を確かめる IT が無い" in result.stderr

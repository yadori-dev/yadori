"""導入した命令と配布用のモジュール起動から案内を読む。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", [False, True], ids=["console", "module"])
class TestHelp:
    def _run(
        self, tmp_path: Path, module: bool, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        command = (
            [sys.executable, "-I", "-m", "yadori"]
            if module
            else [str(Path(sys.executable).parent / "yadori")]
        )
        return subprocess.run(
            [*command, *args],
            cwd=tmp_path,
            env={**os.environ, "YADORI_HOME": str(tmp_path / "dweller")},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_st_077_001_it_077_001_help_before_setup(self, tmp_path: Path, module: bool) -> None:
        done = self._run(tmp_path, module, ["--help"])
        assert done.returncode == 0
        assert done.stderr == ""
        assert "使い方:" in done.stdout
        assert "python -m" not in done.stdout
        assert "uv run" not in done.stdout
        for command in (
            "claude",
            "agy",
            "codex",
            "discord",
            "dream",
            "state",
            "measure",
            "evals draft",
        ):
            assert f"yadori {command}" in done.stdout
        assert "宿りを起こして話す" in done.stdout
        for option in (
            "--at",
            "--eval",
            "--embedding",
            "--floor",
            "--recent",
            "--limit",
            "--from",
            "--out",
            "--append",
        ):
            assert option in done.stdout
        assert not (tmp_path / "dweller").exists()

    @pytest.mark.parametrize(
        "args",
        [
            ["unknown"],
            ["--help", "extra"],
            ["state", "--at"],
            ["state", "--at", "invalid"],
            ["measure", "--unknown", "1"],
            ["evals", "draft", "--from", "missing"],
            ["claude", "extra"],
            ["codex", "extra"],
            ["agy", "extra"],
            ["discord", "extra"],
            ["dream", "extra"],
        ],
    )
    def test_st_077_002_it_077_001_invalid_arguments(
        self, tmp_path: Path, module: bool, args: list[str]
    ) -> None:
        done = self._run(tmp_path, module, args)
        assert done.returncode == 1
        assert done.stdout == ""
        assert "使い方:" in done.stderr
        assert not (tmp_path / "dweller").exists()

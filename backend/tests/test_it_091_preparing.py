"""起動前の選択、人物固定、並行と中断を実際の境界から確かめる。"""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import override

import pytest

from yadori.adapter.tool.preparation_call import PreparationCall
from yadori.infrastructure.preparation_settings import PreparationSetting
from yadori.infrastructure.preparing import Preparing
from yadori.infrastructure.settings import Configuration, SettingsFile


class Terminal(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


def settings(home: Path) -> None:
    home.mkdir(exist_ok=True)
    _ = (home / "settings.toml").write_text(
        'version=1\nactive="a"\n[people.a]\nname="そら"\nnickname="そら"\n'
        + 'owner="架空"\nidentity="そらです"\n[people.b]\nname="別人"\n'
        + 'nickname="別人"\nowner="架空"\nidentity="別人です"\n'
    )


def configured(home: Path, mode: str = "auto") -> Configuration:
    settings(home)
    config = Configuration(home).load()
    person = config.person()
    person["preparation"] = {
        "mode": mode,
        "dream": True,
        "sources": [{"provider": "codex", "path": "logs"}],
    }
    config.put_person(person)
    config.save()
    return config


def test_ST_091_008_IT_091_004_初回選択を保存して固定人物で順に準備する(tmp_path: Path) -> None:
    settings(tmp_path)
    (tmp_path / "logs").mkdir()
    configuration = Configuration(tmp_path).load()
    output = Terminal()
    PreparationSetting(Terminal("1\ncodex\n" + str(tmp_path / "logs") + "\ny\n"), output).configure(
        configuration
    )
    configuration.save()
    chosen = SettingsFile(tmp_path).read()
    changed = Configuration(tmp_path).load()
    changed.data["active"] = "b"
    changed.save()
    calls: list[str] = []
    assert Preparing(io.StringIO(), output, lambda step: calls.append(step) or 0).run(
        configuration, chosen
    )
    assert calls == ["import", "dream"]
    assert "そら" in output.getvalue()
    assert SettingsFile(tmp_path).read().dweller.id == "b"


def test_ST_091_009_IT_091_004_毎回選択と非対話と今回スキップ(tmp_path: Path) -> None:
    config = configured(tmp_path, "ask")
    chosen = SettingsFile(tmp_path).read()
    calls: list[str] = []

    def run(step: str) -> int:
        calls.append(step)
        return 0

    assert Preparing(Terminal("n\ny\n"), Terminal(), run).run(config, chosen)
    assert calls == ["dream"]
    calls.clear()
    assert Preparing(io.StringIO(), io.StringIO(), run).run(config, chosen)
    assert not calls
    assert Preparing(Terminal(), Terminal(), run).run(config, chosen, "skip")
    assert not calls
    assert Preparing(io.StringIO(), io.StringIO(), run).run(config, chosen, "auto")
    assert calls == ["import", "dream"]


def test_ST_091_011_IT_091_004_並行と破損では準備しない(tmp_path: Path) -> None:
    config = configured(tmp_path)
    chosen = SettingsFile(tmp_path).read()
    calls: list[str] = []

    def runner(step: str) -> int:
        calls.append(step)
        return 0

    digest = hashlib.sha256(b"a").hexdigest()[:24]
    with (tmp_path / f"prepare-{digest}.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert Preparing(io.StringIO(), io.StringIO(), runner).run(config, chosen)
        assert not calls
    _ = chosen.memories_path.write_text("not sqlite")
    assert not Preparing(io.StringIO(), io.StringIO(), runner).run(config, chosen)
    assert not calls


def test_ST_091_010_ST_091_011_取込失敗でも自身の夢は独立して進む(tmp_path: Path) -> None:
    config = configured(tmp_path)
    chosen = SettingsFile(tmp_path).read()
    calls: list[str] = []
    assert Preparing(io.StringIO(), io.StringIO(), lambda step: calls.append(step) or 1).run(
        config, chosen
    )
    assert calls == ["import", "dream"]


def test_ST_091_009_IT_091_004_取消時に実際の子プロセス群を止める(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = (
        "import subprocess,sys,time; "
        + "p=subprocess.Popen([sys.executable,'-c','import signal,time;"
        + "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']); "
        + f"open({str(pid_file)!r},'w').write(str(p.pid)); time.sleep(30)"
    )
    timer = threading.Timer(1, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            _ = PreparationCall([sys.executable, "-c", script]).run()
    finally:
        timer.cancel()
        timer.join()
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    assert not result.stdout.strip() or result.stdout.lstrip().startswith("Z")

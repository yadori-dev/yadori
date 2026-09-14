"""通常の対話起動だけで、固定した人物の取り込みと夢を一度準備する。"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import sys
from collections.abc import Callable
from typing import TextIO, final

from yadori.adapter.tool.preparation_call import PreparationCall
from yadori.infrastructure.dream import Dreamer
from yadori.infrastructure.importing import Importer
from yadori.infrastructure.preparation_settings import Mode, PreparationOptions, PreparationSetting
from yadori.infrastructure.settings import Configuration, NotSettled, Settings, SettingsFile


@final
class Preparing:
    def __init__(
        self,
        reading: TextIO | None = None,
        writing: TextIO | None = None,
        runner: Callable[[str], int] | None = None,
    ) -> None:
        self._reading = reading or sys.stdin
        self._writing = writing or sys.stdout
        self._runner = runner or self._child

    def configure(self, configuration: Configuration, override: Mode | None) -> Configuration:
        if override == "skip" or PreparationOptions.read(configuration) is not None:
            return configuration
        if not self._interactive():
            self._say("起動前の準備は未設定です。yadori settingで選べます。今回は会話へ進みます")
            return configuration
        changed = configuration.copy()
        try:
            PreparationSetting(self._reading, self._writing).configure(changed)
            changed.validate()
            self._say("選んだ取り込み元と動作をこの人物へ保存します [y/N]: ", end="")
            if self._reading.readline().strip().lower() == "y":
                changed.save()
                return changed
        except (EOFError, KeyboardInterrupt):
            self._say("起動前の設定を取り消しました。今回は会話へ進みます")
        return configuration

    def run(
        self, configuration: Configuration, settings: Settings, override: Mode | None = None
    ) -> bool:
        options = PreparationOptions.read(configuration)
        if override == "skip" or options is None or (override or options.mode) == "skip":
            return True
        mode = override or options.mode
        if mode == "ask" and not self._interactive():
            self._say("起動前の実行選択ができない入力です。準備を飛ばして会話へ進みます")
            return True
        digest = hashlib.sha256(settings.dweller.id.encode()).hexdigest()[:24]
        with (settings.home / f"prepare-{digest}.lock").open("w") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._say("この人物は別の起動で準備中です。今回は会話へ進みます")
                return True
            try:
                self._healthy(settings)
                self._say(f"起動前の準備: {settings.dweller.name}（Ctrl+Cで残りを飛ばし会話へ）")
                for step, enabled in (("import", bool(options.sources)), ("dream", options.dream)):
                    if not enabled:
                        continue
                    label = (
                        "外部会話の取り込み（新規200往復まで）"
                        if step == "import"
                        else "自身の会話の夢（一回分）"
                    )
                    if mode == "ask" and not self._choose(label):
                        continue
                    self._say(f"{label}を始めます")
                    code = self._runner(step)
                    self._healthy(settings)
                    if code:
                        self._say(f"{label}は完了しませんでした。未処理を残して続けます")
            except (EOFError, KeyboardInterrupt):
                self._say("起動前の準備を中断しました。確定した分を残して会話を始めます")
            except (OSError, sqlite3.Error, NotSettled) as trouble:
                self._say(f"記憶の保存先を確かめられません。起動しません: {trouble}")
                return False
        return True

    @staticmethod
    def worker(step: str) -> int:
        if not os.environ.get("YADORI_SETTINGS_SNAPSHOT"):
            raise NotSettled("起動前の固定設定がありません")
        settings = SettingsFile().read()
        configuration = Configuration().load()
        options = PreparationOptions.read(configuration)
        if options is None:
            return 0
        if step == "import":
            return Importer(settings.home).run(
                options.sources, settings.dweller.id, True, 200, settings
            )
        return Dreamer(settings.home).run(settings)

    def _child(self, step: str) -> int:
        return PreparationCall([sys.executable, "-I", "-m", "yadori", "_prepare-step", step]).run()

    def _healthy(self, settings: Settings) -> None:
        if not settings.memories_path.exists():
            return
        connection = sqlite3.connect(
            settings.memories_path.resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise NotSettled("記憶の保存先が壊れています")
        finally:
            connection.close()

    def _interactive(self) -> bool:
        return self._reading.isatty() and self._writing.isatty()

    def _choose(self, label: str) -> bool:
        self._say(f"{label}を実行しますか？ [Y/n]: ", end="")
        line = self._reading.readline()
        if not line:
            raise EOFError
        return line.strip().lower() != "n"

    def _say(self, text: str, end: str = "\n") -> None:
        _ = self._writing.write(text + end)
        _ = self._writing.flush()

"""起動前の処理を専用のプロセス群で呼び、中断時は子も止める。"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import final


@final
class PreparationCall:
    def __init__(self, command: list[str]) -> None:
        self._command = command

    def run(self) -> int:
        with subprocess.Popen(self._command, start_new_session=True) as process:
            try:
                return process.wait()
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    _ = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    # 親だけ先に終わっても、SIGTERMを無視する子を残さない。
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    _ = process.wait()
                raise

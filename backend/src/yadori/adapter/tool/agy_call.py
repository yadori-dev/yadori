"""agy を専用の設定と空の作業場所で一回だけ呼ぶ。会話のフックは使わない。"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import final

from yadori.adapter.tool.agy_notice import AgyJson
from yadori.adapter.tool.agy_session import AgySession
from yadori.adapter.tool.claude_code import ToolCallFailed


@final
class AgyCall:
    def __init__(self, model: str | None, wait_seconds: int) -> None:
        self._model = model
        self._wait_seconds = wait_seconds

    @property
    def model(self) -> str:
        return f"agy:{self._model or 'default'}"

    def ask(self, preface: str, spoken: str) -> str:
        try:
            with tempfile.TemporaryDirectory(prefix="yadori-agy-call-") as temporary:
                root = Path(temporary)
                work = root / "work"
                work.mkdir()
                return self._ask_in(root, work, preface, spoken)
        except (OSError, ValueError, subprocess.SubprocessError) as trouble:
            raise ToolCallFailed(f"agy の一回実行が失敗しました: {trouble}") from trouble

    def _ask_in(self, root: Path, work: Path, preface: str, spoken: str) -> str:
        session = AgySession(root, cwd=work, internal=True)
        prepared = session.prepare()
        try:
            argv = [
                *prepared.argv,
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--disable-slash-commands",
            ]
            if self._model:
                argv.extend(["--model", self._model])
            request = {"event": "user", "message": {"content": preface + "\n\n" + spoken}}
            done = subprocess.run(
                argv,
                input=json.dumps(request, ensure_ascii=False) + "\n",
                cwd=work,
                env=prepared.environment,
                capture_output=True,
                text=True,
                timeout=self._wait_seconds,
                check=False,
            )
            if done.returncode:
                raise ToolCallFailed(
                    f"agy の一回実行が失敗しました（終了状態 {done.returncode}）。再送しません"
                )
            return self._reply(done.stdout)
        finally:
            # 内部実行は記憶へ追加しないため、失敗時も会話の回復領域へ残さない。
            session.finish(prepared, 0)

    def _reply(self, output: str) -> str:
        results: list[str] = []
        for line in output.splitlines():
            event = AgyJson.object(line)
            if event.get("event") != "result":
                continue
            result = AgyJson.object(json.dumps(event.get("result")))
            if result.get("status") != "SUCCESS":
                raise ToolCallFailed("agy が結果を返せませんでした。再送しません")
            results.append(AgyJson.text(result, "response"))
        if len(results) != 1:
            raise ToolCallFailed("agy の返事が一件ではありません")
        return results[0]

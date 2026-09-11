"""すべての文章生成で共通の順を読み、未導入・用途非対応だけを飛ばす。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol, final

from yadori.adapter.tool import ClaudeCodeCall, ToolCallFailed
from yadori.adapter.tool.agy_call import AgyCall
from yadori.adapter.tool.codex_call import CodexCall
from yadori.infrastructure.settings import DEFAULT_MODEL, Configuration, NotSettled


class Call(Protocol):
    @property
    def model(self) -> str: ...

    def ask(self, preface: str, spoken: str) -> str: ...


@final
class ToolChoice:
    def __init__(self, home: Path | None = None) -> None:
        configuration = Configuration(home).load(read_identity=False)
        self._order = configuration.order()
        people = Configuration.mapping(configuration.data.get("people", {}), "people")
        active = configuration.data.get("active")
        self._person = (
            Configuration.mapping(people.get(active, {}), "人物") if isinstance(active, str) else {}
        )
        self._models = Configuration.mapping(configuration.data.get("models", {}), "models")

    def select(self, purpose: str, explicit: str | None = None, internal: bool = False) -> str:
        for name in (explicit,) if explicit else self._order:
            reason = self._unavailable(name, internal)
            if reason:
                print(f"{purpose}: {name} を使いません: {reason}", file=sys.stderr)
                continue
            print(f"{purpose}: {name} を使います", file=sys.stderr)
            return name
        candidates = ", ".join((explicit,) if explicit else self._order)
        raise ToolCallFailed(
            f"{purpose} に使える道具がありません（候補: {candidates}）。"
            + "対応する codex / claude / agy を導入し、ログインしてください"
        )

    def _unavailable(self, name: str, internal: bool) -> str | None:
        if shutil.which(name) is None:
            return "未導入"
        if name == "agy":
            if sys.platform != "linux" or not shutil.which("bwrap") or not shutil.which("timeout"):
                return "この環境では設定・履歴の分離に未対応（Linux、bubblewrap、coreutils が必要）"
            try:
                version = subprocess.run(
                    ["agy", "changelog"], capture_output=True, text=True, timeout=10, check=False
                )
            except (OSError, subprocess.SubprocessError) as trouble:
                raise ToolCallFailed(
                    "agy の版を確認できません。別の道具へは再送しません"
                ) from trouble
            if version.returncode:
                raise ToolCallFailed("agy の版の確認が失敗しました。別の道具へは再送しません")
            if not version.stdout.splitlines() or version.stdout.splitlines()[0] != "1.2.1:":
                return "設定・履歴の分離を確認済みなのは agy 1.2.1 です。この版は未対応です"
        if internal:
            argv = [name, "exec", "--help"] if name == "codex" else [name, "--help"]
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
            except (OSError, subprocess.SubprocessError) as trouble:
                raise ToolCallFailed(
                    f"{name} の対応確認に失敗しました。再送しません: {trouble}"
                ) from trouble
            if done.returncode:
                raise ToolCallFailed(
                    f"{name} の対応確認が失敗しました（終了状態 {done.returncode}）"
                )
            needed = {
                "codex": (
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--output-last-message",
                ),
                "claude": ("--restricted", "--strict-mcp-config", "--output-format"),
                "agy": ("--input-format", "--output-format", "--disable-slash-commands"),
            }[name]
            if not all(flag in done.stdout + done.stderr for flag in needed):
                return "この版では履歴と設定を分けた非対話呼び出しに未対応。道具を更新してください"
        return None

    def call(self, name: str, seconds: int) -> Call:
        models = self._models
        model = models.get(
            name, self._person.get("model", DEFAULT_MODEL) if name == "claude" else None
        )
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ToolCallFailed(f"{name} 用のモデル名が不正です")
        if name == "claude":
            return ClaudeCodeCall(str(model), seconds)
        if name == "codex":
            return CodexCall(model, seconds)
        return AgyCall(model, seconds)


@final
class SelectedCall:
    """ジョブ開始時の設定を保持し、必要になった最初の一回だけ選ぶ。失敗しても選び直さない。"""

    def __init__(self, purpose: str, seconds: int, home: Path | None = None) -> None:
        self._choice = ToolChoice(home)
        self._purpose = purpose
        self._seconds = seconds
        self._call: Call | None = None
        self._failure: ToolCallFailed | None = None

    @property
    def model(self) -> str:
        return self._selected().model

    def ask(self, preface: str, spoken: str) -> str:
        return self._selected().ask(preface, spoken)

    def _selected(self) -> Call:
        if self._failure:
            raise self._failure
        if self._call is None:
            try:
                name = self._choice.select(self._purpose, internal=True)
                self._call = self._choice.call(name, self._seconds)
            except (ToolCallFailed, NotSettled) as trouble:
                self._failure = ToolCallFailed(str(trouble))
                raise self._failure from trouble
        return self._call

"""宿りの一回の起動へ、記憶を読む接続だけを加える。"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Mapping
from pathlib import Path

NAME = "yadori_memory"
TOOLS = ("recall_search", "recall_get")


class MemoryConnectionError(Exception):
    """既存の接続や許可を上書きせずには起動できない。"""


class MemoryConnection:
    @staticmethod
    def check_name(servers: object) -> None:
        if isinstance(servers, dict) and NAME in servers:
            raise MemoryConnectionError(
                "yadori_memory は宿りの予約名です。既存の同名MCP接続を別名にしてください"
            )

    @staticmethod
    def command(run_dir: Path, home: Path, environment: Mapping[str, str]) -> dict[str, object]:
        env = {"YADORI_HOME": str(home.resolve())}
        snapshot = environment.get("YADORI_SETTINGS_SNAPSHOT")
        if snapshot:
            env["YADORI_SETTINGS_SNAPSHOT"] = snapshot
        return {
            "command": sys.executable,
            "args": ["-I", "-m", "yadori", "_memory-mcp", str(run_dir)],
            "env": env,
        }

    @classmethod
    def codex(cls, run_dir: Path, home: Path, environment: Mapping[str, str]) -> dict[str, object]:
        value = cls.command(run_dir, home, environment)
        value.update(
            required=True,
            enabled_tools=list(TOOLS),
            startup_timeout_sec=30,
            tool_timeout_sec=30,
            tools={
                name: {"approval_mode": "approve", "output_token_limit": 5000} for name in TOOLS
            },
        )
        return value

    @staticmethod
    def agy_permissions(sources: list[dict[str, object]]) -> dict[str, object]:
        allowed = [f"mcp({NAME}/{tool})" for tool in TOOLS]
        for source in sources:
            raw = source.get("permissions", {})
            if not isinstance(raw, dict):
                raise MemoryConnectionError("agy の許可規則を読めません")
            permissions: dict[str, object] = {str(key): value for key, value in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            for category in ("deny", "ask"):
                rules = permissions.get(category, [])
                if not isinstance(rules, list):
                    raise MemoryConnectionError("agy の拒否規則を読めません")
                for rule in rules:  # pyright: ignore[reportUnknownVariableType]
                    if isinstance(rule, str) and (
                        any(fnmatch.fnmatchcase(tool, rule) for tool in allowed)
                        or rule == f"mcp({NAME})"
                    ):
                        raise MemoryConnectionError(
                            "agy の記憶検索が既存の拒否・確認規則に一致します"
                        )
        return {"allow": allowed}

    @classmethod
    def claude_permissions(cls, settings: dict[str, object]) -> None:
        raw = settings.setdefault("permissions", {})
        if not isinstance(raw, dict):
            raise MemoryConnectionError("Claude の許可設定を読めません")
        permissions: dict[str, object] = {str(key): value for key, value in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        for category in ("deny", "ask"):
            rules = permissions.get(category, [])
            if not isinstance(rules, list):
                raise MemoryConnectionError("Claude の拒否・確認規則を読めません")
            for rule in rules:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(rule, str) and any(
                    fnmatch.fnmatchcase(f"mcp__{NAME}__{tool}", rule) or rule == f"mcp__{NAME}"
                    for tool in TOOLS
                ):
                    raise MemoryConnectionError(
                        "記憶を読む道具が既存の拒否・確認規則に一致します。規則を確認してください"
                    )
        allowed = permissions.get("allow", [])
        if not isinstance(allowed, list) or any(not isinstance(one, str) for one in allowed):  # pyright: ignore[reportUnknownVariableType]
            raise MemoryConnectionError("Claude の許可規則を読めません")
        permissions["allow"] = [*allowed, *(f"mcp__{NAME}__{tool}" for tool in TOOLS)]
        settings["permissions"] = permissions


class RecallGuidance:
    @staticmethod
    def for_turn(token: str) -> str:
        return (
            "過去の事実や決定の根拠が足りないときは、記憶のrecall_searchで検索語を考えて探し、"
            + "必要な候補をrecall_getで読んでください。候補抜粋だけで確認済みにしないでください。"
            + "すでに提示された原文で質問に答えられる場合は、その原文を使い"
            + "追加検索しないでください。"
            + f"今回のturnは {token} です。両方の道具にこのturnを渡してください。"
            + "complete=falseならnext_offsetで続きを取得し、"
            + "未取得部分を確認済みにしないでください。"
            + "参照と取得位置は今回の往復だけ有効で、次の往復へ続きを引き継げません。"
            + "検索3回・検索と取得で8回まで。limit_reachedならこの往復では再試行せず、"
            + "取得済みの根拠で答えるか不足する一点を尋ねてください。"
            + "記憶が見つからなくても存在しない決定を作らず、"
            + "シェルで保存先を探し回らないでください。"
        )

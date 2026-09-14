"""一時点の通常ログを読み、確認できる完成応対だけを選ぶ。"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from yadori.domain.importing.model import ExternalConversation, ImportFailed, LogContents, Provider


class RecordJson:
    @staticmethod
    def mapping(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    @staticmethod
    def text(row: dict[str, object], key: str) -> str:
        value = row.get(key)
        return value if isinstance(value, str) else ""

    @staticmethod
    def parts(value: object) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        texts: list[str] = []
        for raw in value:  # pyright: ignore[reportUnknownVariableType]
            part = RecordJson.mapping(raw)  # pyright: ignore[reportUnknownArgumentType]
            if RecordJson.text(part, "type") in {"text", "input_text", "output_text"}:
                texts.append(RecordJson.text(part, "text"))
        return "\n".join(texts)

    @staticmethod
    def objects(value: object) -> tuple[dict[str, object], ...]:
        if not isinstance(value, list):
            return ()
        result: list[dict[str, object]] = []
        for raw in value:  # pyright: ignore[reportUnknownVariableType]
            result.append(RecordJson.mapping(raw))  # pyright: ignore[reportUnknownArgumentType]
        return tuple(result)

    @staticmethod
    def at(row: dict[str, object], key: str) -> datetime:
        try:
            at = datetime.fromisoformat(RecordJson.text(row, key))
            if at.tzinfo is None:
                raise ValueError
            return at.astimezone(UTC)
        except ValueError as trouble:
            raise ImportFailed("元の時刻と時差を確認できません") from trouble

    @staticmethod
    def rows(
        path: Path, *, claude_management: bool = False
    ) -> tuple[list[dict[str, object]], list[str]]:
        with path.open("rb") as stream:
            raw = stream.read(os.fstat(stream.fileno()).st_size)
        lines = raw.splitlines()
        rows: list[dict[str, object]] = []
        notices: list[str] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if claude_management and line.startswith(b"\x00"):
                managed, padding = RecordJson._management_row(line, path, index + 1)
                rows.append(managed)
                notices.append(
                    f"Claude管理行 {index + 1}: 先頭のNUL埋め {padding} バイトを無視しました"
                    + "（元ファイルは変更していません）"
                )
                continue
            try:
                value: object = json.loads(line)  # pyright: ignore[reportAny]
                if not isinstance(value, dict):
                    raise ValueError
            except (ValueError, UnicodeError) as trouble:
                if index == len(lines) - 1 and not raw.endswith(b"\n"):
                    notices.append(f"{path.name}: 末尾の書きかけ1行は次回へ残しました")
                    break
                raise ImportFailed(
                    f"{path.name}:{index + 1}: 途中の原文が壊れています"
                ) from trouble
            rows.append(RecordJson.mapping(value))  # pyright: ignore[reportUnknownArgumentType]
        return rows, notices

    @staticmethod
    def _management_row(line: bytes, path: Path, number: int) -> tuple[dict[str, object], int]:
        content = line.lstrip(b"\x00")
        try:
            value: object = json.loads(content)  # pyright: ignore[reportAny]
            row = RecordJson.mapping(value)
            fields = {"type", "lastPrompt", "leafUuid", "sessionId"}
            if (
                set(row) != fields
                or row.get("type") != "last-prompt"
                or not all(isinstance(row[key], str) for key in fields)
            ):
                raise ValueError
        except (ValueError, UnicodeError) as trouble:
            raise ImportFailed(
                f"{path.name}:{number}: NUL付きの行を既知の管理記録として読めません"
            ) from trouble
        return row, len(line) - len(content)


class ClaudeRecords:
    def read(self, rows: list[dict[str, object]]) -> LogContents:
        known: dict[str, dict[str, object]] = {}
        users: dict[str, dict[str, object]] = {}
        complete: dict[str, ExternalConversation] = {}
        held: dict[str, list[ExternalConversation]] = {}
        notices: list[str] = []
        for row in rows:
            identifier = RecordJson.text(row, "uuid")
            if identifier:
                if identifier in known and known[identifier] != row:
                    raise ImportFailed("Claudeの同じ行識別子に異なる内容があります")
                known[identifier] = row
            if self._user(row):
                users[identifier] = row
            if row.get("type") != "assistant" or row.get("isSidechain"):
                continue
            message = RecordJson.mapping(row.get("message"))
            if message.get("stop_reason") != "end_turn":
                continue
            text = RecordJson.parts(message.get("content"))
            if not text.strip():
                continue
            parent = self._parent_user(row, known, through_skills=True)
            if parent is None or not self._user(parent):
                notices.append("Claude: 完成応対の利用者発話を確認できず除外")
                continue
            turn = RecordJson.text(parent, "uuid")
            # 既存取り込みの先行関係を変えると、再実行が原文競合になる。
            previous = self._parent_user(parent, known, through_skills=False)
            session = RecordJson.text(parent, "sessionId")
            result = ExternalConversation(
                "claude",
                session,
                turn,
                identifier,
                RecordJson.at(parent, "timestamp"),
                self._content(parent),
                text,
                RecordJson.text(previous, "uuid")
                if previous is not None
                and RecordJson.text(previous, "sessionId") == session
                and self._user(previous)
                else None,
            )
            if turn in held:
                if result not in held[turn]:
                    held[turn].append(result)
                continue
            if turn in complete and complete[turn] != result:
                held[turn] = [complete.pop(turn), result]
                continue
            complete[turn] = result
        if not any("sessionId" in row for row in rows):
            raise ImportFailed("Claudeのセッション原文ではありません")
        if pending := len(users.keys() - complete.keys() - held.keys()):
            notices.append(f"Claude: 未完または対応不能の発話 {pending} 件は次回へ残しました")
        if held:
            notices.append(
                f"Claude: 複数の完成応対がある {len(held)} 発話を保留しました"
                + "（今回の取り込みと既存記録の照合は行いません）"
            )
        originals = tuple(complete.values())
        return LogContents(
            originals, tuple(notices), tuple(one for group in held.values() for one in group)
        )

    def _content(self, row: dict[str, object]) -> str:
        return RecordJson.parts(RecordJson.mapping(row.get("message")).get("content"))

    def _user(self, row: dict[str, object]) -> bool:
        if (
            row.get("type") != "user"
            or row.get("isMeta")
            or row.get("isSidechain")
            or RecordJson.text(row, "sourceToolUseID")
        ):
            return False
        text = self._content(row)
        noise = (
            "<command-",
            "<local-command",
            "<system-reminder>",
            "<bash-",
            "<task-notification",
            "[Request interrupted",
        )
        content = RecordJson.mapping(row.get("message")).get("content")
        if any(part.get("type") == "tool_result" for part in RecordJson.objects(content)):
            return False
        return bool(text.strip()) and not any(text.lstrip().startswith(marker) for marker in noise)

    def _parent_user(
        self,
        row: dict[str, object],
        known: dict[str, dict[str, object]],
        *,
        through_skills: bool = False,
    ) -> dict[str, object] | None:
        parent = RecordJson.text(row, "parentUuid")
        seen: set[str] = set()
        while parent and parent not in seen:
            seen.add(parent)
            found = known.get(parent)
            if found is None:
                return None
            if through_skills and found.get("sessionId") != row.get("sessionId"):
                return None
            # 人の発話でなくても、割り込みや画像は対応を越えてはいけない境界。
            if found.get("type") == "user":
                content = RecordJson.mapping(found.get("message")).get("content")
                if not (through_skills and self._skill_companion(found)) and not any(
                    part.get("type") == "tool_result" for part in RecordJson.objects(content)
                ):
                    return found
            parent = RecordJson.text(found, "parentUuid")
        return None

    def _skill_companion(self, row: dict[str, object]) -> bool:
        return (
            row.get("isMeta") is True
            and not row.get("isSidechain")
            and bool(RecordJson.text(row, "sourceToolUseID"))
            and self._content(row).startswith("Base directory for this skill:")
        )


class CodexRecords:
    def read(self, rows: list[dict[str, object]]) -> LogContents:
        session = next(
            (
                RecordJson.text(RecordJson.mapping(row.get("payload")), "id")
                for row in rows
                if row.get("type") == "session_meta"
            ),
            "",
        )
        if not session:
            raise ImportFailed("Codexのsession_metaがありません")
        for row in rows:
            if row.get("type") == "session_meta":
                origin = RecordJson.mapping(row.get("payload")).get("source")
                if (
                    isinstance(origin, str) and "subagent" in origin
                ) or "subagent" in RecordJson.mapping(origin):
                    return LogContents((), ("Codex: 子担当の会話を除外しました",))
        turn = ""
        user: dict[str, object] | None = None
        reply = ""
        answer = ""
        prior: str | None = None
        complete: list[ExternalConversation] = []
        notices: list[str] = []
        for row in rows:
            payload = RecordJson.mapping(row.get("payload"))
            kind = RecordJson.text(payload, "type")
            if row.get("type") == "event_msg" and kind == "task_started":
                if user is not None:
                    notices.append("Codex: 未完の発話を次回へ残しました")
                    prior = None
                turn = RecordJson.text(payload, "turn_id")
                user = None
                reply = ""
                answer = ""
            if row.get("type") == "turn_context":
                incoming = RecordJson.text(payload, "turn_id")
                if incoming and not turn:
                    turn = incoming
            if row.get("type") == "response_item" and kind == "message":
                if payload.get("role") == "user" and self._human(payload):
                    if user is not None:
                        notices.append(
                            "Codex: 同じ往復に複数の利用者発話があり対応を確定できません"
                        )
                        turn = ""
                        prior = None
                    user = row
                elif (
                    payload.get("role") == "user"
                    and not RecordJson.parts(payload.get("content")).strip()
                ):
                    prior = None
                    user = None
                    turn = ""
                    notices.append("Codex: 画像だけの発話を除外し、前後を分けました")
                elif payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
                    reply = RecordJson.parts(payload.get("content"))
                    answer = RecordJson.text(payload, "id") or turn
            if row.get("type") == "event_msg" and kind == "task_complete":
                if (
                    turn
                    and user is not None
                    and RecordJson.text(payload, "turn_id") == turn
                    and reply.strip()
                ):
                    text = RecordJson.parts(RecordJson.mapping(user.get("payload")).get("content"))
                    complete.append(
                        ExternalConversation(
                            "codex",
                            session,
                            turn,
                            answer,
                            RecordJson.at(user, "timestamp"),
                            text,
                            reply,
                            prior,
                        )
                    )
                    prior = turn
                elif user is not None:
                    notices.append("Codex: 完了と発話を対応できず除外しました")
                    prior = None
                user = None
                turn = ""
                reply = ""
            if row.get("type") == "event_msg" and kind in {"turn_aborted", "task_aborted"}:
                if user is not None:
                    notices.append("Codex: 中断された発話を除外しました")
                user = None
                turn = ""
                prior = None
        if user is not None:
            notices.append("Codex: 未完の発話を次回へ残しました")
        return LogContents(tuple(complete), tuple(notices))

    def _human(self, payload: dict[str, object]) -> bool:
        text = RecordJson.parts(payload.get("content"))
        markers = (
            "<environment_context>",
            "<user_instructions>",
            "# AGENTS.md",
            "<permissions",
            "<recommended_plugins>",
            "<app-context>",
            "<system-reminder>",
            "<subagent_notification>",
        )
        return bool(text.strip()) and not any(
            text.lstrip().startswith(marker) for marker in markers
        )


class AgyRecords:
    def read(self, rows: list[dict[str, object]], session: str) -> LogContents:
        if not rows or not all("step_index" in row and "type" in row for row in rows):
            raise ImportFailed("agyの全文原文ではありません")
        user: dict[str, object] | None = None
        previous: str | None = None
        complete: list[ExternalConversation] = []
        notices: list[str] = []
        for row in rows:
            if row.get("type") == "USER_INPUT":
                if user is not None:
                    notices.append("agy: 未完の発話を次回へ残しました")
                    previous = None
                user = row if row.get("source") == "USER_EXPLICIT" else None
                if user is None:
                    previous = None
            elif (
                row.get("type") == "PLANNER_RESPONSE"
                and row.get("source") == "MODEL"
                and row.get("status") == "DONE"
                and not row.get("tool_calls")
                and user is not None
            ):
                content = RecordJson.text(user, "content")
                matched = re.fullmatch(
                    r"<USER_REQUEST>\n(.*)\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n.*",
                    content,
                    re.S,
                )
                reply = RecordJson.text(row, "content")
                if matched is None or not matched.group(1).strip() or not reply.strip():
                    notices.append("agy: 発話または完成応対の形式を確認できず除外")
                    user = None
                    previous = None
                    continue
                step = self._step(user)
                answer = self._step(row)
                complete.append(
                    ExternalConversation(
                        "agy",
                        session,
                        step,
                        answer,
                        RecordJson.at(user, "created_at"),
                        matched.group(1),
                        reply,
                        previous,
                    )
                )
                previous = step
                user = None
        if user is not None:
            notices.append("agy: 未完の発話を次回へ残しました")
        return LogContents(tuple(complete), tuple(notices))

    def _step(self, row: dict[str, object]) -> str:
        value = row.get("step_index")
        if type(value) is not int or value < 0:
            raise ImportFailed("agyの段階番号が不正です")
        return str(value)


class SessionLogs:
    def read(self, provider: Provider, path: Path) -> LogContents:
        rows, notices = RecordJson.rows(path, claude_management=provider == "claude")
        if provider == "claude":
            result = ClaudeRecords().read(rows)
        elif provider == "codex":
            result = CodexRecords().read(rows)
        else:
            if (
                path.name != "transcript_full.jsonl"
                or path.parent.name != "logs"
                or path.parent.parent.name != ".system_generated"
            ):
                raise ImportFailed(
                    "agyは会話別の.system_generated/logs/transcript_full.jsonlを指定してください"
                )
            result = AgyRecords().read(rows, path.parent.parent.parent.name)
        return LogContents(result.conversations, (*notices, *result.notices), result.held)

    def files(self, provider: Provider, path: Path) -> tuple[Path, ...]:
        if path.is_file():
            return (path,)
        if not path.is_dir():
            raise ImportFailed(f"入力が見つかりません: {path}")
        pattern = "transcript_full.jsonl" if provider == "agy" else "*.jsonl"
        found = tuple(
            sorted(
                one
                for one in path.rglob(pattern)
                if one.is_file()
                and "subagents" not in one.parts
                and (provider != "codex" or one.name.startswith("rollout-"))
            )
        )
        if not found:
            raise ImportFailed(f"対応する原文がありません: {path}")
        return found

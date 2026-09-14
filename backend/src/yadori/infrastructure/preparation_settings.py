"""起動前に行うことと取り込み元を、人物ごとに選ぶ。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO, final

from yadori.infrastructure.importing import ImportSource
from yadori.infrastructure.settings import Configuration, NotSettled

Mode = Literal["auto", "ask", "skip"]


@dataclass(frozen=True)
class PreparationOptions:
    mode: Mode
    sources: tuple[ImportSource, ...]
    dream: bool

    @classmethod
    def read(cls, configuration: Configuration) -> PreparationOptions | None:
        value = configuration.person().get("preparation")
        if value is None:
            return None
        row = Configuration.mapping(value, "起動前の準備")
        mode = row.get("mode")
        if not isinstance(mode, str) or mode not in {"auto", "ask", "skip"}:
            raise NotSettled("起動前のmodeはauto/ask/skipを選んでください")
        dream = row.get("dream", True)
        if not isinstance(dream, bool):
            raise NotSettled("起動前のdreamはtrue/falseです")
        raw = row.get("sources", [])
        if not isinstance(raw, list):
            raise NotSettled("取り込み元sourcesは配列です")
        sources: list[ImportSource] = []
        for value in raw:  # pyright: ignore[reportUnknownVariableType]
            source = Configuration.mapping(value, "取り込み元")  # pyright: ignore[reportUnknownArgumentType]
            provider = source.get("provider")
            path = source.get("path")
            if (
                not isinstance(provider, str)
                or provider not in {"claude", "codex", "agy"}
                or not isinstance(path, str)
                or not path.strip()
            ):
                raise NotSettled("取り込み元にはclaude/codex/agyとpathが必要です")
            name = "claude" if provider == "claude" else ("codex" if provider == "codex" else "agy")
            written = Path(path).expanduser()
            sources.append(
                ImportSource(
                    name, written if written.is_absolute() else configuration.home / written
                )
            )
        chosen = "auto" if mode == "auto" else ("ask" if mode == "ask" else "skip")
        return cls(chosen, tuple(sources), dream)


@final
class PreparationSetting:
    def __init__(self, reading: TextIO | None = None, writing: TextIO | None = None) -> None:
        self._reading = reading or sys.stdin
        self._writing = writing or sys.stdout

    def configure(self, configuration: Configuration) -> None:
        self._say(
            "起動前に、外部会話の取り込み→宿り自身の夢を行えます。夢の対象は対話する道具へ渡ります"
        )
        choice = self._line("1 自動 / 2 毎回選ぶ / 3 実行しない [1]: ") or "1"
        if choice not in {"1", "2", "3"}:
            raise NotSettled("1から3を選んでください")
        mode = {"1": "auto", "2": "ask", "3": "skip"}[choice]
        sources: list[dict[str, str]] = []
        dream = False
        if mode != "skip":
            providers = self._line("取り込む道具（claude codex agy。空なら取り込みなし）: ").split()
            if len(set(providers)) != len(providers) or any(
                name not in {"claude", "codex", "agy"} for name in providers
            ):
                raise NotSettled("取り込む道具を重複なく指定してください")
            defaults = {
                "claude": Path.home() / ".claude/projects",
                "codex": Path.home() / ".codex/sessions",
                "agy": Path.home() / ".gemini/antigravity-cli/brain",
            }
            for name in providers:
                path = (
                    Path(
                        self._line(
                            f"{name}の取り込み元 [~/{defaults[name].relative_to(Path.home())}]: "
                        )
                        or str(defaults[name])
                    )
                    .expanduser()
                    .resolve()
                )
                if not path.exists():
                    raise NotSettled(f"取り込み元がありません: {path}")
                sources.append({"provider": name, "path": str(path)})
            dream = self._line("自身の未処理会話があるとき夢を見ますか？ [Y/n]: ").lower() != "n"
        person = configuration.person()
        person["preparation"] = {"mode": mode, "sources": sources, "dream": dream}
        configuration.put_person(person)
        _ = PreparationOptions.read(configuration)
        self._say(
            f"起動前の動作: {mode} / 取り込み元 {len(sources)} 件"
            + f" / 自身の夢: {'実行' if dream else 'なし'}"
        )

    def _line(self, prompt: str) -> str:
        _ = self._writing.write(prompt)
        _ = self._writing.flush()
        line = self._reading.readline()
        if not line:
            raise EOFError
        if line.strip() == "/cancel":
            raise EOFError
        return line.strip()

    def _say(self, text: str) -> None:
        _ = self._writing.write(text + "\n")
        _ = self._writing.flush()

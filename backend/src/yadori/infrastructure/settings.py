"""設定を読む。

記憶と名乗りは持ち主の手元に置く。リポジトリへ入れない。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import final

from yadori.domain.memory import Dweller

DEFAULT_HOME = Path.home() / ".yadori"
# 機械が名前で探し、利用者が端末で打つファイルは ASCII にする。
# 名前は用語集のコード名に合わせる。
WHO = "dweller.toml"
NAME_DECLARED = "identity.md"


class NotSettled(Exception):
    """宿りの設定が見つからない、または足りない。"""


DEFAULT_MODEL = "opus"


@final
@dataclass(frozen=True)
class Settings:
    home: Path
    dweller: Dweller
    name_declared: str
    model: str

    @property
    def memories_path(self) -> Path:
        return self.home / "memories.sqlite"


@final
class SettingsFile:
    """手元の置き場から、誰を起こすかと名乗りを読む。"""

    def __init__(self, home: Path | None = None) -> None:
        self._home: Path = home or Path(os.environ.get("YADORI_HOME", str(DEFAULT_HOME)))

    def read(self) -> Settings:
        """設定を読む。

        - 誰であるかを読む
        - 名乗りを読む
        """
        written = self._written()
        return Settings(
            home=self._home,
            dweller=self._who(written),
            name_declared=self._name_declared(),
            model=self._model(written),
        )

    def _written(self) -> dict[str, object]:
        path = self._home / WHO
        if not path.exists():
            raise NotSettled(
                f"{path} がありません。次の形で作ってください。\n"
                + '  name = "田中れいな"\n  nickname = "れいな"\n  owner = "あなたの名前"'
            )
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def _model(self, written: dict[str, object]) -> str:
        """どの模型で考えるか。宿りが誰であるかとは別の、動かし方の設定である。"""
        chosen = written.get("model", DEFAULT_MODEL)
        return str(chosen)

    def _who(self, written: dict[str, object]) -> Dweller:
        """宿りの名前、呼び名、持ち主を読む。"""
        path = self._home / WHO
        missing = [key for key in ("name", "nickname", "owner") if key not in written]
        if missing:
            raise NotSettled(f"{path} に {'、'.join(missing)} がありません。")
        return Dweller(
            id=str(written.get("id", written["nickname"])),
            owner=str(written["owner"]),
            name=str(written["name"]),
            nickname=str(written["nickname"]),
        )

    def _name_declared(self) -> str:
        """名乗りの文章を読む。項目に割らず、一続きの文章として扱う。"""
        path = self._home / NAME_DECLARED
        if not path.exists():
            raise NotSettled(
                f"{path} がありません。その宿りが誰であるかを、項目に分けず"
                + "一続きの文章で書いてください。"
            )
        written = path.read_text(encoding="utf-8").strip()
        if not written:
            raise NotSettled(f"{path} が空です。")
        return written

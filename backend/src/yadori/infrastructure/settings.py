"""設定を読む。

記憶と名乗りは持ち主の手元に置く。リポジトリへ入れない。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from yadori.domain.memory import Dweller

DEFAULT_HOME = Path.home() / ".yadori"
WHO = "宿り.toml"
NAME_DECLARED = "名乗り.md"


class NotSettled(Exception):
    """宿りの設定が見つからない、または足りない。"""


@dataclass(frozen=True)
class Settings:
    home: Path
    dweller: Dweller
    name_declared: str

    @property
    def memories_path(self) -> Path:
        return self.home / "記憶.sqlite"


def read_settings(home: Path | None = None) -> Settings:
    """手元の設定から、誰を起こすかと名乗りを読む。

    - 置き場を決める
    - 誰であるかを読む
    - 名乗りを読む
    """
    at = home or Path(os.environ.get("YADORI_HOME", DEFAULT_HOME))
    dweller = _who(at)
    return Settings(home=at, dweller=dweller, name_declared=_name_declared(at))


def _who(home: Path) -> Dweller:
    """宿りの名前、呼び名、持ち主を読む。"""
    path = home / WHO
    if not path.exists():
        raise NotSettled(
            f"{path} がありません。次の形で作ってください。\n"
            '  name = "田中れいな"\n  nickname = "れいな"\n  owner = "あなたの名前"'
        )
    written = tomllib.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("name", "nickname", "owner") if key not in written]
    if missing:
        raise NotSettled(f"{path} に {'、'.join(missing)} がありません。")
    return Dweller(
        id=str(written.get("id", written["nickname"])),
        owner=str(written["owner"]),
        name=str(written["name"]),
        nickname=str(written["nickname"]),
    )


def _name_declared(home: Path) -> str:
    """名乗りの文章を読む。項目に割らず、一続きの文章として扱う。"""
    path = home / NAME_DECLARED
    if not path.exists():
        raise NotSettled(
            f"{path} がありません。その宿りが誰であるかを、項目に分けず"
            "一続きの文章で書いてください。"
        )
    written = path.read_text(encoding="utf-8").strip()
    if not written:
        raise NotSettled(f"{path} が空です。")
    return written

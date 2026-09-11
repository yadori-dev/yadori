"""設定を読む。

記憶と名乗りは持ち主の手元に置く。リポジトリへ入れない。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from yadori.domain.memory import Dweller

DEFAULT_HOME = Path.home() / ".yadori"
# 機械が名前で探し、利用者が端末で打つファイルは ASCII にする。
# 名前は用語集のコード名に合わせる。
WHO = "dweller.toml"
NAME_DECLARED = "identity.md"
DISCORD = "discord.toml"


class NotSettled(Exception):
    """宿りの設定が見つからない、または足りない。"""


DEFAULT_MODEL = "opus"


@final
@dataclass(frozen=True)
class DiscordSettings:
    """Discord と繋ぐための設定。トークンは持ち主の手元から読み、外へ出さない（ADR-019）。"""

    token: str
    owner_id: int

    @override
    def __repr__(self) -> str:
        # 人が読む形にしてもトークンを出さない。記録や画面へ紛れ込ませないため。
        return f"DiscordSettings(token=<伏せた>, owner_id={self.owner_id})"


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

    @property
    def models_path(self) -> Path:
        return self.home / "models"


@final
class SettingsFile:
    """`YADORI_HOME` から、誰を起こすかと名乗りを読む。"""

    def __init__(self, home: Path | None = None) -> None:
        self._home: Path = home or Path(os.environ.get("YADORI_HOME", str(DEFAULT_HOME)))

    @property
    def models_path(self) -> Path:
        """手元で動かす AIモデルのファイルの保存先（`YADORI_HOME` の下の `models/`）。

        宿りの設定が無くても決まる。
        """
        return self._home / "models"

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

    def discord(self) -> DiscordSettings:
        """Discord のトークンと、話しかけてよい持ち主のユーザーIDを読む。

        無い・欠けている・形が違うときは、何をどう書けばよいかを添えて断る。
        """
        path = self._home / DISCORD
        if not path.exists():
            raise NotSettled(f"{path} がありません。{self._how_to_write_discord()}")
        try:
            with path.open("rb") as opened:
                written: dict[str, object] = tomllib.load(opened)
        except tomllib.TOMLDecodeError as broken:
            # 読めない理由には書きかけの中身が入りうる。トークンを持つファイルなので添えない。
            raise NotSettled(
                f"{path} の書き方が壊れています。{self._how_to_write_discord()}"
            ) from broken
        token, owner_id = written.get("token"), written.get("owner_id")
        if not isinstance(token, str) or not token:
            raise NotSettled(f"{path} に token がありません。{self._how_to_write_discord()}")
        if not isinstance(owner_id, int) or isinstance(owner_id, bool):
            raise NotSettled(
                f"{path} の owner_id が数ではありません。{self._how_to_write_discord()}"
            )
        return DiscordSettings(token=token, owner_id=owner_id)

    def _how_to_write_discord(self) -> str:
        return (
            "次の形で作ってください。\n"
            + '  token = "<Discord の bot のトークン>"\n'
            + "  owner_id = <自分のユーザーID>"
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
        """どのAIモデルで考えるか。宿りが誰であるかとは別の、動かし方の設定である。"""
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

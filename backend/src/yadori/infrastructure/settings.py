"""設定を読む。

記憶と名乗りは持ち主の手元に置く。リポジトリへ入れない。
"""

from __future__ import annotations

import copy
import fcntl
import os
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

import tomlkit

from yadori.domain.memory import Dweller

DEFAULT_HOME = Path.home() / ".yadori"
# 機械が名前で探し、利用者が端末で打つファイルは ASCII にする。
# 名前は用語集のコード名に合わせる。
WHO = "dweller.toml"
NAME_DECLARED = "identity.md"
DISCORD = "discord.toml"
CONFIGURATION = "settings.toml"
DEFAULT_PRIORITY = ("codex", "claude", "agy")


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
        configuration = Configuration(self._home).load()
        written = configuration.require_person()
        declared = written.get("identity", "")
        if not isinstance(declared, str) or not declared.strip():
            raise NotSettled("名乗りがありません。yadori setting で設定してください")
        return Settings(
            home=self._home,
            dweller=self._who(written),
            name_declared=declared.strip(),
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

    def _model(self, written: dict[str, object]) -> str:
        """どのAIモデルで考えるか。宿りが誰であるかとは別の、動かし方の設定である。"""
        chosen = written.get("model", DEFAULT_MODEL)
        return str(chosen)

    def _who(self, written: dict[str, object]) -> Dweller:
        return Dweller(
            id=str(written["id"]),
            owner=str(written["owner"]),
            name=str(written["name"]),
            nickname=str(written["nickname"]),
        )


@final
class Configuration:
    """編集用の一括設定。読取不能と値不足を分け、確認した世代だけを保存する。"""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path(os.environ.get("YADORI_HOME", str(DEFAULT_HOME)))
        self.path = self.home / CONFIGURATION
        self.data: dict[str, object] = {}
        self._original: dict[Path, bytes | None] = {}

    def load(self, read_identity: bool = True, snapshot: bool = True) -> Configuration:
        frozen = os.environ.get("YADORI_SETTINGS_SNAPSHOT")
        if snapshot and frozen and Path(frozen).parent.parent == self.home.resolve():
            self.data = self._toml(Path(frozen).read_bytes(), Path(frozen))
            return self
        raw = self._read(self.path)
        if raw is not None:
            self.data = self._toml(raw, self.path)
            if type(self.data.get("version")) is not int or self.data.get("version") != 1:
                raise NotSettled(f"{self.path}: 対応していない設定の版です。変更しません")
            if read_identity and "active" not in self.data and not self.data.get("people"):
                initial = self._legacy(True)
                self.data["active"] = initial["active"]
                self.data["people"] = initial["people"]
        else:
            self.data = self._legacy(read_identity)
        return self

    def _legacy(self, read_identity: bool) -> dict[str, object]:
        who = self.home / WHO
        raw = self._read(who)
        person = {} if raw is None else self._toml(raw, who)
        identifier = person.get("id", person.get("nickname"))
        if "id" in person and (not isinstance(identifier, str) or not identifier.strip()):
            raise NotSettled("既存の人物の id が不正です。元の記憶の id を確認して修正してください")
        if not isinstance(identifier, str) or not identifier.strip():
            identifier = uuid.uuid4().hex
        person["id"] = identifier
        identity = self._read(self.home / NAME_DECLARED) if read_identity else None
        try:
            person["identity"] = "" if identity is None else identity.decode("utf-8")
        except UnicodeError as trouble:
            raise NotSettled("identity.md を読めません。元ファイルを修正してください") from trouble
        return {"version": 1, "active": identifier, "people": {identifier: person}}

    def _read(self, path: Path) -> bytes | None:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = None
        except OSError as trouble:
            raise NotSettled(f"{path} を読めません。上書きしません: {trouble}") from trouble
        self._original[path] = raw
        return raw

    def _toml(self, raw: bytes, path: Path) -> dict[str, object]:
        try:
            return tomllib.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as trouble:
            raise NotSettled(
                f"{path} の書式を読めません。元ファイルを修正してください"
            ) from trouble

    def people(self) -> dict[str, object]:
        return self.mapping(self.data.get("people"), "people")

    def person(self) -> dict[str, object]:
        active = self.data.get("active")
        people = self.people()
        if not isinstance(active, str) or active not in people:
            raise NotSettled(
                "active に選択できる人物がありません。settings.toml を確認してください"
            )
        person = self.mapping(people[active], "人物")
        identifier = person.get("id", active)
        if identifier != active:
            raise NotSettled("people のキーと人物の id が一致しません。変更せず確認してください")
        person["id"] = active
        return person

    @staticmethod
    def mapping(value: object, name: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise NotSettled(f"{name} は表で指定してください")
        return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    @staticmethod
    def priority(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not value:
            raise NotSettled("道具の優先順は codex、claude、agy の空でない配列にしてください")
        result: list[str] = []
        for name in value:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(name, str) or name not in DEFAULT_PRIORITY or name in result:
                raise NotSettled("道具の優先順に未知の名前または重複があります")
            result.append(name)
        return tuple(result)

    def order(self) -> tuple[str, ...]:
        return self.priority(self.data.get("priority", DEFAULT_PRIORITY))

    def put_person(self, person: dict[str, object]) -> None:
        people = self.people()
        people[str(self.data["active"])] = person
        self.data["people"] = people

    def copy(self) -> Configuration:
        edited = Configuration(self.home)
        edited.data = copy.deepcopy(self.data)
        edited._original = self._original.copy()
        return edited

    def require_person(self) -> dict[str, object]:
        person = self.person()
        for key in ("name", "nickname", "owner", "identity", "id"):
            value = person.get(key)
            if not isinstance(value, str) or not value.strip():
                raise NotSettled(f"{key} が未設定または不正です。yadori setting で設定してください")
        return person

    def validate(self) -> None:
        _ = self.require_person()
        _ = self.order()

    def save(self) -> None:
        self.validate()
        text = tomlkit.dumps(self.data)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.home / "settings.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            for path, original in self._original.items():
                try:
                    current = path.read_bytes()
                except FileNotFoundError:
                    current = None
                if current != original:
                    raise NotSettled("編集中に設定が変更されました。保存せず読み直してください")
            with tempfile.NamedTemporaryFile(dir=self.home, delete=False) as opened:
                temporary = Path(opened.name)
                try:
                    _ = opened.write(text.encode("utf-8"))
                    opened.flush()
                    os.fsync(opened.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
        self._original = {self.path: text.encode("utf-8")}

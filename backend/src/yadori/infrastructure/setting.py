"""人物と共通の道具順を、AI を呼ばずに対話で編集する。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TextIO, final

from yadori.infrastructure.settings import (
    DEFAULT_PRIORITY,
    Configuration,
    NotSettled,
    SettingsFile,
)


class Cancelled(Exception):
    """編集中の項目を捨てて戻る。"""


@final
class Setting:
    def __init__(
        self,
        home: Path | None = None,
        reading: TextIO | None = None,
        writing: TextIO | None = None,
    ) -> None:
        self._home = home
        self._reading = reading or sys.stdin
        self._writing = writing or sys.stdout

    def ensure(self) -> bool:
        try:
            _ = SettingsFile(self._home).read()
            return True
        except NotSettled as missing:
            self._say(str(missing))
        if not self._reading.isatty() or not self._writing.isatty():
            self._say("対話可能な端末で yadori setting を実行してください。道具は起動しません")
            return False
        return self.run(resume=True) == 0

    def run(self, resume: bool = False) -> int:
        if not self._reading.isatty() or not self._writing.isatty():
            self._say("設定には対話可能な端末が必要です。端末で yadori setting を実行してください")
            return 1
        try:
            configuration = Configuration(self._home).load(snapshot=False)
            self._say(f"保存先: {configuration.path}（記憶は同じ場所の memories.sqlite）")
            self._say("変更は次の起動から反映します。Discord は再起動してください")
            self._say("/cancel で項目を取消。入力終了・Ctrl-C では未保存の編集を捨てて終了します")
            try:
                configuration.validate()
            except NotSettled as missing:
                self._say(f"設定を補います: {missing}")
                configuration = self._initial(configuration)
            if resume:
                _ = SettingsFile(self._home).read()
                return 0
            self._menu(configuration)
            return 0
        except (EOFError, KeyboardInterrupt, Cancelled):
            self._say("未保存の編集を取り消しました。道具は起動しません")
            return 1
        except (NotSettled, OSError) as trouble:
            self._say(f"設定を保存できません: {trouble}")
            return 1

    def _initial(self, original: Configuration) -> Configuration:
        changed = original.copy()
        person = changed.person()
        for key, label in (("name", "名前"), ("nickname", "呼び名"), ("owner", "持ち主")):
            person[key] = self._text(label, person.get(key))
        person["identity"] = self._identity(person.get("identity"))
        changed.put_person(person)
        changed.data["priority"] = list(self._order(changed))
        changed.validate()
        if not self._save(changed):
            raise Cancelled
        return changed

    def _menu(self, configuration: Configuration) -> None:
        while True:
            person = configuration.person()
            self._say(f"\n現在の人物: {person.get('name')}（呼び名: {person.get('nickname')}）")
            self._say("1 名前を変更する\n2 性格を変更する\n3 別の人物へ切り替える")
            self._say("4 デフォルトのモデル順を変更する\n5 終了する")
            try:
                choice = self._line("何を変更しますか？ [1-5]: ")
                if choice == "5":
                    return
                changed = configuration.copy()
                if choice == "1":
                    self._names(changed)
                elif choice == "2":
                    person = changed.person()
                    person["identity"] = self._identity(person.get("identity"))
                    changed.put_person(person)
                elif choice == "3":
                    self._switch(changed)
                elif choice == "4":
                    changed.data["priority"] = list(self._order(changed))
                else:
                    self._say("1 から 5 を入力してください")
                    continue
                changed.validate()
                if self._save(changed):
                    configuration = changed
            except Cancelled:
                self._say("編集を取り消してメニューへ戻ります")
            except NotSettled as trouble:
                self._say(f"保存していません: {trouble}")

    def _names(self, configuration: Configuration) -> None:
        self._say("1 名前だけ\n2 呼び名だけ\n3 持ち主だけ")
        choice = self._line("変更する項目 [1-3]: ")
        fields = {"1": ("name", "名前"), "2": ("nickname", "呼び名"), "3": ("owner", "持ち主")}
        if choice not in fields:
            raise NotSettled("1 から 3 を入力してください")
        key, label = fields[choice]
        person = configuration.person()
        person[key] = self._text(label, person.get(key))
        configuration.put_person(person)

    def _switch(self, configuration: Configuration) -> None:
        others = [key for key in configuration.people() if key != configuration.data["active"]]
        if not others:
            self._say("別の人物が登録されていません。settings.toml の people に人物を用意できます")
            raise Cancelled
        for number, identifier in enumerate(others, 1):
            person = Configuration.mapping(configuration.people()[identifier], "人物")
            self._say(f"{number} {person.get('name', identifier)}（{identifier}）")
        choice = self._line("切り替え先の番号: ")
        if not choice.isdecimal() or not 1 <= int(choice) <= len(others):
            raise NotSettled("表示された番号を選んでください")
        configuration.data["active"] = others[int(choice) - 1]
        self._say("次の起動から選んだ人物の設定・記憶・状態を使います。現在の人物の記憶も残ります")

    def _order(self, configuration: Configuration) -> tuple[str, ...]:
        self._say("デフォルトのモデル順は道具の優先順です。個別の AIモデル名ではありません")
        self._say(f"現在値: {configuration.data.get('priority', list(DEFAULT_PRIORITY))}")
        for name in DEFAULT_PRIORITY:
            status = "見つかりました（ログインは起動時に確認）" if shutil.which(name) else "未導入"
            self._say(f"{name}: {status}")
        self._say("例: claude codex agy。候補は省けます。空で確定すると現在の順を保持します")
        while True:
            given = self._line("優先順: ")
            try:
                return (
                    Configuration.priority(given.replace(",", " ").split())
                    if given
                    else configuration.order()
                )
            except NotSettled as trouble:
                self._say(str(trouble))

    def _identity(self, current: object) -> str:
        self._say(f"現在の名乗り（性格・話し方）:\n{current or '未設定'}")
        self._say("新しい文章を複数行で入力してください。単独の . で確定、未入力なら現在値を保持")
        lines: list[str] = []
        while True:
            line = self._line("", strip=False)
            if line != ".":
                lines.append(line)
                continue
            result = "\n".join(lines) if lines else current
            if isinstance(result, str) and result.strip():
                return result
            self._say("名乗りを空にはできません。文章を入力してください")
            lines = []

    def _text(self, label: str, current: object) -> str:
        self._say(f"{label}の現在値: {current if current is not None else '未設定'}")
        while True:
            given = self._line(f"{label}（空なら保持）: ")
            value = given or current
            if isinstance(value, str) and value.strip():
                return value
            self._say(f"{label}を空にはできません")

    def _save(self, configuration: Configuration) -> bool:
        person = configuration.person()
        self._say(
            f"\n保存する人物: {person['name']}\n呼び名: {person['nickname']}"
            + f"\n持ち主: {person['owner']}"
        )
        self._say(
            f"名乗り:\n{person['identity']}\n共通の道具順: {' → '.join(configuration.order())}"
        )
        if self._line("保存しますか？ [y/N]: ").lower() != "y":
            self._say("保存せず戻ります")
            return False
        configuration.save()
        self._say("保存しました。移行後は settings.toml を使います。旧設定ファイルは残しています")
        if not any(shutil.which(name) for name in configuration.order()):
            self._say(
                "道具が未導入でも設定は保存済みです。"
                + "codex / claude / agy を導入しログインしてください"
            )
        return True

    def _line(self, prompt: str, strip: bool = True) -> str:
        _ = self._writing.write(prompt)
        _ = self._writing.flush()
        line = self._reading.readline()
        if not line:
            raise EOFError
        text = line.rstrip("\r\n")
        if text.strip() == "/cancel":
            raise Cancelled
        return text.strip() if strip else text

    def _say(self, text: str) -> None:
        _ = self._writing.write(text + "\n")
        _ = self._writing.flush()

"""設定の操作から、取消・移行・人物の継続と分離を確認する。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import override
from unittest.mock import patch

import pytest

from tests.sora import Ticking
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.domain.memory import Moved
from yadori.infrastructure.entry import Entry
from yadori.infrastructure.setting import Setting
from yadori.infrastructure.settings import Configuration, NotSettled, SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation


class TerminalInput(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


def legacy(home: Path) -> None:
    _ = (home / "dweller.toml").write_text(
        'name="空"\nnickname="そら"\nowner="架空の持ち主"\nmodel="opus"\nextra="保持"\n'
    )
    _ = (home / "identity.md").write_text("わたしは空です。園芸が好きです。")


def edit(home: Path, lines: str, resume: bool = False) -> tuple[int, str]:
    output = TerminalInput()
    code = Setting(home, TerminalInput(lines), output).run(resume=resume)
    return code, output.getvalue()


def test_ST_079_001_初回保存と項目編集は同じ保存を使う(tmp_path: Path) -> None:
    code, output = edit(tmp_path, "空\nそら\n架空の持ち主\n丁寧に話す。\n園芸が好き。\n.\n\ny\n5\n")
    assert code == 0 and "保存しました" in output
    original = SettingsFile(tmp_path).read()
    assert original.name_declared == "丁寧に話す。\n園芸が好き。"
    assert Configuration(tmp_path).load().order() == ("codex", "claude", "agy")
    assert edit(tmp_path, "1\n1\n青空\ny\n5\n")[0] == 0
    renamed = SettingsFile(tmp_path).read()
    assert renamed.dweller.name == "青空"
    assert renamed.dweller.nickname == original.dweller.nickname
    assert renamed.dweller.id == original.dweller.id
    assert renamed.name_declared == original.name_declared


@pytest.mark.parametrize("ending", ["/cancel\n", "", "空\n", "空\nそら\n持ち主\n名乗り\n.\n\nn\n"])
def test_ST_079_001_初回の取消と入力終了は何も保存しない(tmp_path: Path, ending: str) -> None:
    assert edit(tmp_path, ending)[0] == 1
    assert not (tmp_path / "settings.toml").exists()
    assert not (tmp_path / "memories.sqlite").exists()


def test_ST_079_001_手書き設定の有効値を保ち不足だけ補える(tmp_path: Path) -> None:
    _ = (tmp_path / "dweller.toml").write_text('name="空"\nnickname="そら"\nextra="保持"\n')
    _ = (tmp_path / "identity.md").write_text("既存の文章\n複数行")
    assert edit(tmp_path, "\n\n持ち主\n.\n\ny\n", resume=True)[0] == 0
    settings = SettingsFile(tmp_path).read()
    assert settings.dweller.id == "そら" and settings.dweller.owner == "持ち主"
    assert settings.name_declared == "既存の文章\n複数行"
    assert Configuration(tmp_path).load().person()["extra"] == "保持"


@pytest.mark.parametrize("broken", ['name="', 'name=4\nnickname="そら"\nowner="持ち主"'])
def test_ST_079_001_不正設定では起動せず原本を保持する(tmp_path: Path, broken: str) -> None:
    _ = (tmp_path / "dweller.toml").write_text(broken)
    _ = (tmp_path / "identity.md").write_text("名乗り")
    with pytest.raises(NotSettled):
        _ = SettingsFile(tmp_path).read()
    assert edit(tmp_path, "")[0] == 1
    assert (tmp_path / "dweller.toml").read_text() == broken


def test_ST_079_001_取消からメニューへ戻り次の項目だけ保存する(tmp_path: Path) -> None:
    legacy(tmp_path)
    assert edit(tmp_path, "1\n1\n捨てる名前\nn\n2\n/cancel\n4\nclaude codex\ny\n5\n")[0] == 0
    settings = SettingsFile(tmp_path).read()
    assert settings.dweller.name == "空"
    assert Configuration(tmp_path).load().order() == ("claude", "codex")


def test_IT_079_001_保存失敗と同時編集では旧設定を保持する(tmp_path: Path) -> None:
    legacy(tmp_path)
    old = (tmp_path / "dweller.toml").read_bytes()
    with patch("os.replace", side_effect=OSError("書込失敗")):
        assert edit(tmp_path, "1\n1\n青空\ny\n")[0] == 1
    assert not (tmp_path / "settings.toml").exists()
    assert (tmp_path / "dweller.toml").read_bytes() == old
    first = Configuration(tmp_path).load()
    second = Configuration(tmp_path).load()
    first.save()
    with pytest.raises(NotSettled, match="編集中"):
        second.save()


def test_ST_079_002_IT_079_001_呼び名と名乗りを変えても人物の記憶と版が続く(tmp_path: Path) -> None:
    legacy(tmp_path)
    settings = SettingsFile(tmp_path).read()
    memories = SqliteMemories(settings.memories_path)
    try:
        Startup(tmp_path).settle(memories, settings)
        conversation = Conversation(memories, CharacterPairs(), Ticking())
        _ = conversation.remember("そら", "トマトの支柱", "覚えました", Moved.unmoved())
        assert edit(tmp_path, "1\n2\nあお\ny\n2\n新しい名乗り\n.\ny\n5\n")[0] == 0
        settings = SettingsFile(tmp_path).read()
        Startup(tmp_path).settle(memories, settings)
        assert settings.dweller.id == "そら" and settings.dweller.nickname == "あお"
        assert memories.count_episodes("そら") == 1
        assert memories.current_identity("そら").version == 2  # pyright: ignore[reportOptionalMemberAccess]
        assert memories.identity_at("そら", 1).text == "わたしは空です。園芸が好きです。"  # pyright: ignore[reportOptionalMemberAccess]
    finally:
        memories.close()


def test_ST_079_002_人物を往復しても原文を混ぜない(tmp_path: Path) -> None:
    legacy(tmp_path)
    config = Configuration(tmp_path).load()
    people = config.people()
    people["umi"] = {"name": "海", "nickname": "うみ", "owner": "持ち主", "identity": "海です"}
    config.data["people"] = people
    config.save()
    memories = SqliteMemories(tmp_path / "memories.sqlite")
    try:
        for identifier, text in (("そら", "トマト"), ("umi", "船")):
            settings = SettingsFile(tmp_path).read()
            Startup(tmp_path).settle(memories, settings)
            _ = Conversation(memories, CharacterPairs(), Ticking()).remember(
                identifier,
                text,
                "覚えました",
                Moved(0.6 if identifier == "そら" else -0.4, "別々の動き"),
            )
            assert edit(tmp_path, "3\n1\ny\n5\n")[0] == 0
        assert SettingsFile(tmp_path).read().dweller.id == "そら"
        assert [episode.utterance for episode in memories.recent("そら", 5)] == ["トマト"]
        assert [episode.utterance for episode in memories.recent("umi", 5)] == ["船"]
        assert [shift.delta for shift in memories.shifts("そら")] == [0.6]
        assert [shift.delta for shift in memories.shifts("umi")] == [-0.4]
    finally:
        memories.close()


def test_ST_079_001_非対話の未設定起動とhelpは道具を起こさない(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YADORI_HOME", str(tmp_path))
    with patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()):
        for command in ([], ["codex"], ["claude"], ["agy"], ["chat"], ["setting"]):
            assert Entry(["yadori", *command]).run() == 1
        assert Entry(["yadori", "--help"]).run() == 0
    assert not (tmp_path / "settings.toml").exists()


def test_IT_079_003_起動時の写しは後から人物を編集しても変わらない(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomlkit

    legacy(tmp_path)
    configuration = Configuration(tmp_path).load()
    run = tmp_path / "settings-run-test"
    run.mkdir()
    snapshot = run / "settings.toml"
    _ = snapshot.write_text(tomlkit.dumps(configuration.data))
    monkeypatch.setenv("YADORI_SETTINGS_SNAPSHOT", str(snapshot))
    # 編集の入口だけは写しではなく最新の設定を読む。
    assert edit(tmp_path, "1\n1\n新しい名前\ny\n5\n")[0] == 0
    assert SettingsFile(tmp_path).read().dweller.name == "空"
    monkeypatch.delenv("YADORI_SETTINGS_SNAPSHOT")
    assert SettingsFile(tmp_path).read().dweller.name == "新しい名前"

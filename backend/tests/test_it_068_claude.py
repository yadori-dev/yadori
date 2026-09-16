"""IT-068: 普段の Claude Code と混ざらず、宿りとして一往復を続ける。"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.sora import fixed
from yadori.adapter.embedding import CharacterPairs
from yadori.adapter.store import SqliteMemories
from yadori.adapter.tool import ClaudeWords
from yadori.domain.conversation import CannotSpeak
from yadori.domain.memory import (
    Character,
    Dream,
    Dreamed,
    Episode,
    Found,
    Gist,
    Identity,
    Mood,
    Recollection,
    Retrieval,
    State,
)
from yadori.infrastructure.claude import ClaudeCompanion, ClaudeHook
from yadori.infrastructure.start import Startup


def _object(path: Path) -> dict[str, object]:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]
    assert isinstance(parsed, dict)
    checked: dict[str, object] = {}
    for key, value in parsed.items():  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(key, str)
        checked[key] = value
    return checked


def _home(path: Path) -> Path:
    path.mkdir()
    _ = (path / "dweller.toml").write_text(
        'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
        encoding="utf-8",
    )
    _ = (path / "identity.md").write_text("わたしはそらです。園芸を好みます。\n", encoding="utf-8")
    return path


def _hook(
    event: str,
    payload: Mapping[str, object],
    run_dir: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    startup = Startup(home, default=fixed(CharacterPairs()))
    return ClaudeHook(event, run_dir, home, startup).run()


def test_IT_068_001_作業場所の同名Pythonパッケージをフック入口より先に読まない(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    impostor = workspace / "yadori"
    impostor.mkdir(parents=True)
    touched = tmp_path / "impostor-ran"
    _ = (impostor / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(touched)!r}).touch()\n", encoding="utf-8"
    )
    entry = Path(sys.executable).with_name("yadori").resolve()

    done = subprocess.run(
        [str(entry), "unknown"], cwd=workspace, text=True, capture_output=True, check=False
    )

    assert done.returncode == 1 and "使い方" in done.stderr
    assert not touched.exists()


def test_IT_068_002_一往復を一度だけ覚えて同じ停止通知は増やさない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    run_dir = home / "claude" / "sessions" / "one-run"
    run_dir.mkdir(parents=True)
    session_id, prompt_id = str(uuid.uuid4()), str(uuid.uuid4())

    assert (
        _hook(
            "UserPromptSubmit",
            {"session_id": session_id, "prompt_id": prompt_id, "prompt": "トマトを植えました"},
            run_dir,
            home,
            monkeypatch,
        )
        == 0
    )
    context = capsys.readouterr().out.strip()
    assert len(context.encode("utf-8")) <= 9000
    assert "わたしはそらです" in context and "【気持ち】" in context

    stopped = {
        "session_id": session_id,
        "prompt_id": prompt_id,
        "last_assistant_message": "育つのが楽しみですね。\n【気持ち】 +0.2 楽しみ",
    }
    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    changed = {
        **stopped,
        "last_assistant_message": "育つのが楽しみですね。\n【気持ち】 -0.2 違う動き",
    }
    assert _hook("Stop", changed, run_dir, home, monkeypatch) == 0
    assert "以前と異なる返事または気持ち" in capsys.readouterr().out

    memories = SqliteMemories(home / "memories.sqlite")
    try:
        assert memories.count_episodes("sora") == 1
        episode = memories.recent("sora", 1)[0]
        assert episode.utterance == "トマトを植えました"
        assert episode.reply == "育つのが楽しみですね。"
        assert episode.recalled_at is not None and episode.source == f"claude:{prompt_id}"
        assert len(memories.shifts("sora")) == 1
    finally:
        memories.close()


def test_IT_068_003_応答の無い発話は次の発話と終了時に捨てる(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    run_dir = home / "claude" / "sessions" / "one-run"
    run_dir.mkdir(parents=True)
    session_id = str(uuid.uuid4())
    first, second = str(uuid.uuid4()), str(uuid.uuid4())

    assert (
        _hook(
            "UserPromptSubmit",
            {"session_id": session_id, "prompt_id": first, "prompt": "途中で止める発話"},
            run_dir,
            home,
            monkeypatch,
        )
        == 0
    )
    _ = capsys.readouterr()
    assert (
        _hook(
            "UserPromptSubmit",
            {"session_id": session_id, "prompt_id": second, "prompt": "次の発話"},
            run_dir,
            home,
            monkeypatch,
        )
        == 0
    )
    _ = capsys.readouterr()
    assert _hook("SessionEnd", {"session_id": session_id}, run_dir, home, monkeypatch) == 0

    memories = SqliteMemories(home / "memories.sqlite")
    try:
        assert memories.count_episodes("sora") == 0
    finally:
        memories.close()
    assert not (run_dir / "pending" / f"{session_id}.json").exists()


def test_IT_068_003_同じ識別子へ異なる発話が届いたら古い発話と混ぜない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    run_dir = home / "claude" / "sessions" / "one-run"
    run_dir.mkdir(parents=True)
    session_id, prompt_id = str(uuid.uuid4()), str(uuid.uuid4())
    first = {"session_id": session_id, "prompt_id": prompt_id, "prompt": "最初の発話"}
    assert _hook("UserPromptSubmit", first, run_dir, home, monkeypatch) == 0
    _ = capsys.readouterr()

    changed = {"session_id": session_id, "prompt_id": prompt_id, "prompt": "異なる発話"}
    assert _hook("UserPromptSubmit", changed, run_dir, home, monkeypatch) == 2
    assert "同じ prompt_id へ異なる発話" in capsys.readouterr().err
    pending = _object(run_dir / "pending" / f"{session_id}.json")
    assert pending["utterance"] == "最初の発話"


def test_IT_068_003_保存に失敗した返事は止めて未完から再び保存できる(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    run_dir = home / "claude" / "sessions" / "one-run"
    run_dir.mkdir(parents=True)
    session_id, prompt_id = str(uuid.uuid4()), str(uuid.uuid4())
    submitted = {"session_id": session_id, "prompt_id": prompt_id, "prompt": "覚えてね"}
    assert _hook("UserPromptSubmit", submitted, run_dir, home, monkeypatch) == 0
    _ = capsys.readouterr()
    connection = sqlite3.connect(home / "memories.sqlite")
    _ = connection.execute(
        "CREATE TRIGGER refuse_shift BEFORE INSERT ON shift"
        + " BEGIN SELECT RAISE(ABORT, '動きを保存できない'); END"
    )
    connection.close()
    stopped = {
        "session_id": session_id,
        "prompt_id": prompt_id,
        "last_assistant_message": "覚えます。\n【気持ち】 +0.1 うれしい",
    }

    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    assert '"decision": "block"' in capsys.readouterr().out
    memories = SqliteMemories(home / "memories.sqlite")
    try:
        assert memories.count_episodes("sora") == 0
    finally:
        memories.close()
    assert (run_dir / "pending" / f"{session_id}.json").is_file()

    connection = sqlite3.connect(home / "memories.sqlite")
    _ = connection.execute("DROP TRIGGER refuse_shift")
    connection.close()
    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    memories = SqliteMemories(home / "memories.sqlite")
    try:
        assert memories.count_episodes("sora") == 1
    finally:
        memories.close()
    assert not (run_dir / "pending" / f"{session_id}.json").exists()


def test_IT_068_003_完了印の保存に失敗しても未完を残して再送を照合する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    run_dir = home / "claude" / "sessions" / "one-run"
    run_dir.mkdir(parents=True)
    session_id, prompt_id = str(uuid.uuid4()), str(uuid.uuid4())
    submitted = {"session_id": session_id, "prompt_id": prompt_id, "prompt": "覚えてね"}
    assert _hook("UserPromptSubmit", submitted, run_dir, home, monkeypatch) == 0
    _ = capsys.readouterr()
    stopped = {
        "session_id": session_id,
        "prompt_id": prompt_id,
        "last_assistant_message": "覚えます。\n【気持ち】 +0.1 うれしい",
    }
    original_replace = Path.replace
    failed = False

    def failing_once(path: Path, target: Path) -> Path:
        nonlocal failed
        if target.suffix == ".done" and not failed:
            failed = True
            raise OSError("完了印を書けない")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", failing_once)

    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    assert "再送確認を残せませんでした" in capsys.readouterr().out
    assert (run_dir / "pending" / f"{session_id}.json").is_file()
    memories = SqliteMemories(home / "memories.sqlite")
    try:
        assert memories.count_episodes("sora") == 1
    finally:
        memories.close()

    assert _hook("Stop", stopped, run_dir, home, monkeypatch) == 0
    assert not (run_dir / "pending" / f"{session_id}.json").exists()


def test_IT_068_003_以前の保存先の移行に失敗したら元の形で残して起動しない(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _home(tmp_path / "home")
    path = home / "memories.sqlite"
    connection = sqlite3.connect(path)
    _ = connection.executescript(
        """
        CREATE TABLE dweller (id TEXT PRIMARY KEY, owner TEXT, name TEXT, nickname TEXT);
        CREATE TABLE identity (dweller_id TEXT, version INTEGER, text TEXT,
          PRIMARY KEY (dweller_id, version));
        CREATE TABLE episode (id INTEGER PRIMARY KEY AUTOINCREMENT, dweller_id TEXT,
          utterance TEXT, reply TEXT, identity_version INTEGER, happened_at TEXT);
        CREATE TABLE shift (id INTEGER PRIMARY KEY AUTOINCREMENT, dweller_id TEXT, at TEXT,
          delta REAL, cause TEXT, episode_id INTEGER);
        INSERT INTO episode VALUES (1, 'sora', '元の発話', '元の返事', 1,
          '2026-09-01T00:00:00+00:00');
        INSERT INTO shift VALUES (1, 'sora', '2026-09-01T00:00:01+00:00', 0.1, '一つ目', 1);
        INSERT INTO shift VALUES (2, 'sora', '2026-09-01T00:00:02+00:00', 0.2, '二つ目', 1);
        """
    )
    connection.close()
    startup = Startup(home, default=fixed(CharacterPairs()))

    assert ClaudeCompanion(home, tmp_path, startup).run() == 1
    assert "UNIQUE constraint failed" in capsys.readouterr().err

    connection = sqlite3.connect(path)
    rows: list[tuple[object, ...]] = connection.execute("PRAGMA table_info(episode)").fetchall()
    columns = {str(row[1]) for row in rows}
    episode: tuple[str, str] | None = connection.execute(  # pyright: ignore[reportAny]
        "SELECT utterance, reply FROM episode WHERE id = 1"
    ).fetchone()
    connection.close()
    assert "recalled_at" not in columns and "source" not in columns
    assert episode == ("元の発話", "元の返事")


def test_IT_068_004_長い文脈でも最新の一往復を先に残して九千バイトへ収める() -> None:
    at = datetime(2026, 9, 7, tzinfo=UTC)
    previous = Episode(1, "前の発話", "前の返事", 1, at)
    latest = Episode(2, "必ず残す最新の発話", "必ず残す最新の返事", 1, at)
    related = Episode(3, "関係する古い発話" + "長" * 5000, "古い返事" + "長" * 5000, 1, at)
    recollection = Recollection(
        Identity(1, "わたしはそらです"),
        (previous, latest),
        (Found(related, 0.9, Retrieval(0, None), "test"),),
        State(Mood(0), Character(0)),
        None,
        at,
    )

    response = ClaudeWords().hook_response(recollection)

    assert len(response.encode("utf-8")) <= 9000
    assert response.index("必ず残す最新の発話") < response.index("関係する古い発話")
    assert "長さの上限により" in response
    assert "前の発話" not in response


def test_IT_068_004_名乗りと状態だけで上限を越えるなら発話を止める() -> None:
    at = datetime(2026, 9, 7, tzinfo=UTC)
    recollection = Recollection(
        Identity(1, "長" * 5000),
        (),
        (),
        State(Mood(0), Character(0)),
        None,
        at,
    )

    with pytest.raises(CannotSpeak, match="フック上限"):
        _ = ClaudeWords().hook_response(recollection)


def test_IT_068_004_省略を示せないほど名乗りが長ければ発話を止める() -> None:
    at = datetime(2026, 9, 7, tzinfo=UTC)
    words = ClaudeWords()
    smallest = Recollection(
        Identity(1, "x"),
        (),
        (),
        State(Mood(0), Character(0)),
        None,
        at,
    )
    room = 9000 - len(words.hook_response(smallest).encode("utf-8"))
    latest = Episode(1, "直前の発話", "直前の返事", 1, at)
    recollection = Recollection(
        Identity(1, "x" * (room + 1)),
        (latest,),
        (),
        State(Mood(0), Character(0)),
        None,
        at,
    )

    with pytest.raises(CannotSpeak, match="省略を示す文"):
        _ = words.hook_response(recollection)


def test_IT_068_004_省略が不要な短い記憶は上限ぎりぎりでも渡す() -> None:
    at = datetime(2026, 9, 7, tzinfo=UTC)
    words = ClaudeWords()
    dream = Dreamed(Dream(1, at, at, at, 0, 0, None), (Gist("", at, ()),))
    with_dream = Recollection(
        Identity(1, "x"),
        (),
        (),
        State(Mood(0), Character(0)),
        dream,
        at,
    )
    room = 9000 - len(words.hook_response(with_dream).encode("utf-8"))
    tight = Recollection(
        Identity(1, "x" * (room + 1)),
        (),
        (),
        State(Mood(0), Character(0)),
        dream,
        at,
    )

    response = words.hook_response(tight)

    assert len(response.encode("utf-8")) == 9000
    assert "夢で残した要点" in response
    assert "長さの上限により" not in response

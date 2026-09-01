"""INC-031 の結合テスト。

名前の解き方、口の割り方、出自と名前、重さの量り方、取得の前触れ、既定の工場を確かめる。

架空の会話で書く。AIモデルは読み込まず、道具の代わりに語の重なりの並びを返すものを差し込む。
"""

from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.appending import HOW, drafted, first_records
from tests.contending import RURI, Growing, Heavy, Recording, Runner, Stepping
from tests.sora import Steady, Ticking, fixed
from yadori.adapter.embedding import (
    CharacterPairs,
    Choosing,
    DefaultEmbeddings,
    Multilingual,
    NotAnEmbeddingName,
    Size,
    Table,
    Weighing,
)
from yadori.adapter.evaluation import DraftFile
from yadori.adapter.store import InMemoryMemories
from yadori.domain.evaluation import Covered, DrawnWith, Exchange, RecallEval
from yadori.domain.memory import (
    Dweller,
    EmbeddingsUnavailable,
    HowToRecall,
    Prefixes,
    Provenance,
)
from yadori.infrastructure.measure import Measure
from yadori.infrastructure.settings import SettingsFile
from yadori.infrastructure.start import Startup
from yadori.usecase.conversation import Conversation

MINI = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MINI_SHORT = "paraphrase-multilingual-MiniLM-L12-v2"
RURI_SOURCE = "sirasagi62/ruri-v3-30m-ONNX"
SORA = Dweller(id="sora", owner="架空の持ち主", name="そら", nickname="そら")


def _choosing(tmp_path: Path, notes: list[str] | None = None) -> Choosing:
    kept = notes if notes is not None else []
    return Choosing(tmp_path / "models", announcing=kept.append, runner=Runner)


class TestIT031001:
    """名前の解き方と断り方、既定も包まれること。"""

    def test_IT_031_001_名前ごとに種類の分かる出自の包みが返る(self, tmp_path: Path) -> None:
        choosing = _choosing(tmp_path)

        characters = choosing.pick("characters")
        multilingual = choosing.pick("multilingual")
        listed = choosing.pick("ruri-v3-30m")
        by_source = choosing.pick(RURI_SOURCE)
        in_table = choosing.pick(MINI)
        both = choosing.pick("characters+multilingual")
        default = choosing.pick(None)

        assert isinstance(characters, Weighing) and characters.provenance.tool == "character-pairs"
        assert isinstance(multilingual, Weighing) and multilingual.provenance.ai_model == MINI_SHORT
        assert isinstance(listed, Weighing) and listed.provenance.ai_model == RURI_SOURCE
        assert listed.provenance.prefixes == RURI
        assert isinstance(by_source, Weighing) and by_source.provenance == listed.provenance
        assert isinstance(in_table, Weighing) and in_table.provenance.ai_model == MINI_SHORT
        assert in_table.provenance.prefixes is None
        assert isinstance(both, list) and [way.provenance.tool for way in both] == [
            "character-pairs",
            "fastembed",
        ]
        assert isinstance(default, Weighing) and default.provenance.ai_model == RURI_SOURCE
        assert default.provenance.prefixes == RURI
        assert not (tmp_path / "models").exists()

    def test_IT_031_001_工場を差し替えると名前無しの出自が変わる(self, tmp_path: Path) -> None:
        swapped = Choosing(tmp_path / "models", default=fixed(Heavy(ai_model="swapped")))

        picked = swapped.pick(None)

        assert isinstance(picked, Weighing) and picked.provenance.ai_model == "swapped"

    @pytest.mark.parametrize("written", ["ruri", "nobody/nothing"])
    def test_IT_031_001_指せない名前は取得せずに断り指せる名前を教える(
        self, tmp_path: Path, written: str
    ) -> None:
        with pytest.raises(NotAnEmbeddingName) as refused:
            _ = _choosing(tmp_path).pick(written)

        assert "characters、multilingual、ruri-v3-30m、ruri-v3-130m、multilingual-e5-small" in str(
            refused.value
        )
        assert "配布元/名前 で指せます" in str(refused.value)
        assert not (tmp_path / "models").exists()

    def test_IT_031_001_道具が無くても語の重なりは解け対応表の名前は導入の仕方を添えて断る(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 道具を読み込めない状態を、取り込みが失敗する形で作る。
        monkeypatch.setitem(sys.modules, "fastembed", None)
        monkeypatch.setitem(sys.modules, "fastembed.common.model_description", None)
        choosing = _choosing(tmp_path)

        characters = choosing.pick("characters")
        with pytest.raises(EmbeddingsUnavailable, match="uv sync"):
            _ = choosing.pick(MINI)

        assert isinstance(characters, Weighing)


class TestIT031008:
    """登録が繰り返せ、取得先と知らせ先が取得する埋め込みへ届く。"""

    def test_IT_031_008_二度解いても登録で落ちず解いていない一覧の名前は対応表に増えない(
        self, tmp_path: Path
    ) -> None:
        other_before = Table().described("sirasagi62/ruri-v3-130m-ONNX")

        _ = _choosing(tmp_path).pick("ruri-v3-30m")
        _ = _choosing(tmp_path).pick("ruri-v3-30m")

        assert Table().described(RURI_SOURCE) is not None
        # 解いていない一覧の名前は、この解き方では対応表に増えない。
        assert Table().described("sirasagi62/ruri-v3-130m-ONNX") == other_before

    def test_IT_031_008_取得する埋め込みにだけ取得先と知らせ先が届く(self, tmp_path: Path) -> None:
        notes: list[str] = []
        choosing = _choosing(tmp_path, notes)

        listed = choosing.pick("ruri-v3-30m")
        in_table = choosing.pick(MINI)
        characters = choosing.pick("characters")
        assert isinstance(listed, Weighing) and isinstance(in_table, Weighing)
        assert isinstance(characters, Weighing)
        _ = listed.to_remember("トマトを植えました")
        _ = in_table.to_remember("トマトを植えました")
        _ = characters.to_remember("トマトを植えました")

        assert notes == [
            f"取得します: {RURI_SOURCE} 約0.15GB（配布元の表示） → {tmp_path / 'models'}",
            f"取得します: {MINI_SHORT} 約0.22GB（配布元の表示） → {tmp_path / 'models'}",
        ]
        assert characters.weighed().size == Size.absent()


class TestIT031002:
    """側ごとに呼び分けられ、添え書きが側ごとに付いて道具へ渡る。"""

    def test_IT_031_002_覚えると作り直しは覚える側で思い出すは問い合わせ側(self) -> None:
        recording = Recording()
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")
        conversation = Conversation(memories, recording, Ticking(), HowToRecall(1, 5, 0.1))

        _ = conversation.remember(SORA.id, "トマトを植えました", "いいですね")
        _ = conversation.recall(SORA.id, "トマトはどう")
        memories.clear_index(SORA.id)
        rebuilt = conversation.rebuild_index(SORA.id)

        assert rebuilt == 1
        assert recording.remembered == ["トマトを植えました", "トマトを植えました"]
        assert recording.recalled == ["トマトはどう"]

    def test_IT_031_002_添え書きは側ごとの語だけが先頭に付いて道具へ渡る(self) -> None:
        with_prefixes, without = Runner(), Runner()

        prefixed = Multilingual(prefixes=RURI, runner=lambda: with_prefixes)
        plain = Multilingual(runner=lambda: without)
        _ = prefixed.to_remember("トマトを植えました")
        _ = prefixed.to_recall("トマトはどう")
        _ = plain.to_remember("トマトを植えました")
        _ = plain.to_recall("トマトはどう")

        assert with_prefixes.received == [
            "検索文書: トマトを植えました",
            "検索クエリ: トマトはどう",
        ]
        assert without.received == ["トマトを植えました", "トマトはどう"]


class TestIT031003:
    """名前の形と追記の可否に添え書きと配布元が効き、下書きへ往復する。"""

    def test_IT_031_003_添え書き無しの名前は今と一字も変わらず有りは符号が挟まる(self) -> None:
        plain = Provenance(MINI_SHORT, "fastembed", "0.8.0")
        prefixed = Provenance(RURI_SOURCE, "fastembed", "0.8.0", RURI)
        altered = Provenance(
            RURI_SOURCE, "fastembed", "0.8.0", Prefixes("検索文書:", "検索クエリ: ")
        )
        shifted = Provenance("m", "t", "v", Prefixes("ab", "c"))
        unshifted = Provenance("m", "t", "v", Prefixes("a", "bc"))
        leading_zero = Prefixes(remember="覚: ", recall="問100: ")

        assert plain.index_name == "paraphrase-multilingual-MiniLM-L12-v2/fastembed-0.8.0"
        assert prefixed.index_name == f"{RURI_SOURCE}+{RURI.code}/fastembed-0.8.0"
        assert len(RURI.code) == 8 and RURI.code.isascii() and RURI.code.startswith("0")
        assert altered.index_name != prefixed.index_name
        assert shifted.index_name != unshifted.index_name
        assert len(leading_zero.code) == 8 and leading_zero.code.startswith("0")
        assert plain.described == prefixed.described.replace(RURI_SOURCE, MINI_SHORT)

    def test_IT_031_003_添え書きだけの違いは追記を断る違いとして返る(self) -> None:
        before = DrawnWith(Provenance(RURI_SOURCE, "fastembed", "0.8.0", RURI), HOW, "opus")
        now = DrawnWith(Provenance(RURI_SOURCE, "fastembed", "0.8.0"), HOW, "opus")

        assert before.differs_from(now) is not None
        assert "添え書き" in (before.differs_from(now) or "")
        assert before.differs_from(before) is None

    def test_IT_031_003_添え書きは下書きへ往復し鍵の無い前回は添え書き無しとして読む(
        self, tmp_path: Path
    ) -> None:
        prefixed = DrawnWith(Provenance(RURI_SOURCE, "fastembed", "0.8.0", RURI), HOW, "opus")
        plain = DrawnWith(Provenance(MINI_SHORT, "fastembed", "0.8.0"), HOW, "opus")
        recall_eval = RecallEval(3, (Exchange("e001", "x", "y"),), ())
        at = datetime(2026, 1, 1, tzinfo=UTC)

        DraftFile().write(
            tmp_path / "p.toml", recall_eval, Covered(at, ("/a",), (), 1, 1, 0, prefixed)
        )
        DraftFile().write(
            tmp_path / "n.toml", recall_eval, Covered(at, ("/a",), (), 1, 1, 0, plain)
        )
        _, loaded_prefixed = DraftFile().read(tmp_path / "p.toml")
        _, loaded_plain = DraftFile().read(tmp_path / "n.toml")

        assert loaded_prefixed.drawn_with == prefixed
        assert 'remember = "検索文書: "' in (tmp_path / "p.toml").read_text(encoding="utf-8")
        assert "remember" not in (tmp_path / "n.toml").read_text(encoding="utf-8")
        assert loaded_plain.drawn_with.provenance.prefixes is None
        assert loaded_plain.drawn_with.differs_from(plain) is None

    def test_IT_031_003_一覧の出自は配布元を含み対応表の出自は短い(self, tmp_path: Path) -> None:
        choosing = _choosing(tmp_path)

        listed = choosing.pick("ruri-v3-30m")
        in_table = choosing.pick(MINI)

        assert isinstance(listed, Weighing) and listed.provenance.ai_model == RURI_SOURCE
        assert isinstance(in_table, Weighing) and in_table.provenance.ai_model == MINI_SHORT


class TestIT031004:
    """重さの量り方、区切り、口を広げていないこと。"""

    def test_IT_031_004_読み込みは一度で一発話の平均は区切りごとに求め直す(self) -> None:
        heavy = Heavy(loaded_in=1.5, size=Size.of(150))
        # 一発話ごとに 0.1、0.2、0.3、次に 0.4、0.5 かかる時計。
        clock = Stepping([0.0, 0.1, 0.1, 0.3, 0.3, 0.6, 0.6, 1.0, 1.0, 1.5])
        weighing = Weighing(heavy, clock=clock)

        for text in ("a", "b", "c"):
            _ = weighing.to_remember(text)
        first = weighing.weighed()
        _ = weighing.to_recall("d")
        _ = weighing.to_recall("e")
        second = weighing.weighed()
        third = weighing.weighed()

        assert heavy.prepared == 1
        assert first.loaded_in == 1.5 and first.size == Size.of(150)
        assert first.per_text == pytest.approx(0.2)
        assert second.per_text == pytest.approx(0.45)
        assert second.loaded_in == 1.5 and second.size == Size.of(150)
        assert third.per_text is None

    def test_IT_031_004_支度の口が無ければ読み込みと大きさは無しで時間は答える(self) -> None:
        weighing = Weighing(CharacterPairs(), clock=Steady())

        before = weighing.weighed()
        _ = weighing.to_remember("a")
        after = weighing.weighed()

        assert before.per_text is None and before.loaded_in is None
        assert after.size == Size.absent() and after.loaded_in is None
        assert after.per_text == pytest.approx(0.001)

    def test_IT_031_004_出自と名前は包む前後で同じで会話の口は重さを求めない(self) -> None:
        recording = Recording()
        weighing = Weighing(Heavy(prefixes=RURI))
        memories = InMemoryMemories()
        memories.settle(SORA)
        _ = memories.write_identity(SORA.id, "わたしはそらです。")

        # 側ごとの二つと出自と名前だけを持つ差し替えが、会話の口をそのまま通る。
        conversation = Conversation(memories, recording, Ticking())
        _ = conversation.remember(SORA.id, "トマトを植えました", "いいですね")
        found = conversation.recall(SORA.id, "トマトを植えました").found

        assert weighing.provenance == Heavy(prefixes=RURI).provenance
        assert weighing.name == Heavy(prefixes=RURI).name
        assert found == ()


class TestIT031005:
    """取得の前触れと大きさの求め方（差し替えの分。実物は契約テスト）。"""

    def _multilingual(self, tmp_path: Path, notes: list[str]) -> Multilingual:
        return Multilingual(
            model="somebody/some-model",
            ai_model="somebody/some-model",
            cache_dir=tmp_path / "models",
            source="somebody/some-model",
            declared_gb=0.15,
            announcing=notes.append,
            runner=Runner,
        )

    def test_IT_031_005_無ければ前触れが渡り大きさは不明(self, tmp_path: Path) -> None:
        notes: list[str] = []

        prepared = self._multilingual(tmp_path, notes).prepare()

        assert notes == [
            f"取得します: somebody/some-model 約0.15GB（配布元の表示） → {tmp_path / 'models'}"
        ]
        assert prepared.size == Size.unknown()

    def test_IT_031_005_有れば何も告げず大きさは実体のファイルの合計(self, tmp_path: Path) -> None:
        notes: list[str] = []
        fetched_to = tmp_path / "models" / "models--somebody--some-model" / "snapshots" / "x"
        fetched_to.mkdir(parents=True)
        _ = (fetched_to / "a.onnx").write_bytes(b"0" * 700)
        _ = (fetched_to / "b.json").write_bytes(b"0" * 300)
        os.symlink(fetched_to / "a.onnx", fetched_to / "link.onnx")

        prepared = self._multilingual(tmp_path, notes).prepare()

        assert notes == []
        assert prepared.size == Size.of(1000)


class TestIT031006:
    """結果の行の形と順。"""

    def _lines(self, path: Path, ways: Weighing | list[Weighing], twice: bool) -> list[str]:
        written = io.StringIO()
        code = Measure(
            ways,
            eval_path=path,
            baseline=HowToRecall(6, 5, 0.21),
            changed=HowToRecall(6, 5, 0.35) if twice else None,
            writing=written,
        ).run()
        assert code == 0
        return written.getvalue().splitlines()

    def _eval(self, tmp_path: Path) -> Path:
        from tests.test_st_018_measure import EVAL

        path = tmp_path / "recall.toml"
        _ = path.write_text(EVAL, encoding="utf-8")
        return path

    def test_IT_031_006_出自の行と重さの行と条件と要約の順で二度目は一発話が違う(
        self, tmp_path: Path
    ) -> None:
        lines = self._lines(
            self._eval(tmp_path), Weighing(Heavy(prefixes=RURI), clock=Growing()), True
        )

        heads = [line for line in lines if line.startswith("埋め込み: ")]
        weights = [line for line in lines if line.startswith("大きさ ")]
        assert lines[0].startswith("埋め込み: heavy（fake-tool-v1） 添え書き: 覚える「検索文書: 」")
        assert lines[1].startswith("大きさ 0.15GB / 読み込み 1.500秒 / 一発話 ")
        assert lines[2].startswith("条件: ")
        assert lines[3].startswith("2問中")
        assert len(heads) == 2 and len(weights) == 2
        assert weights[0] != weights[1]

    def test_IT_031_006_二つの道は道ごとに二行ずつ出て定めないものは無し(
        self, tmp_path: Path
    ) -> None:
        lines = self._lines(
            self._eval(tmp_path),
            [
                Weighing(Heavy(prefixes=RURI), clock=Steady()),
                Weighing(CharacterPairs(), clock=Steady()),
            ],
            False,
        )

        assert lines[0].startswith("埋め込み: heavy（fake-tool-v1） 添え書き: 覚える")
        assert lines[1].startswith("大きさ 0.15GB")
        assert lines[2] == "埋め込み: AIモデル無し（character-pairs-v1） 添え書き: 無し"
        assert lines[3] == "大きさ 無し / 読み込み 無し / 一発話 0.001秒"
        assert lines[4].startswith("条件: ")


class TestIT031007:
    """既定が一箇所で決まる。"""

    def _home(self, tmp_path: Path) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        _ = (tmp_path / "dweller.toml").write_text(
            'id = "sora"\nname = "そら"\nnickname = "そら"\nowner = "架空の持ち主"\n',
            encoding="utf-8",
        )
        _ = (tmp_path / "identity.md").write_text("わたしはそらです。", encoding="utf-8")
        return tmp_path

    def test_IT_031_007_工場を差し替えると起動も下書きも名前無しの選択も変わる(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = self._home(tmp_path / "home")
        monkeypatch.setenv("YADORI_HOME", str(home))
        swapped = fixed(Heavy(ai_model="swapped"))
        settings = SettingsFile(home).read()

        started = Startup(home, default=swapped).embeddings(settings)
        chosen = Choosing(home / "models", default=swapped).pick(None)
        _, out, _ = drafted(
            first_records(tmp_path / "records"),
            tmp_path / "draft.toml",
            embeddings=Heavy(ai_model="swapped"),
        )

        assert started.provenance.ai_model == "swapped"
        assert isinstance(chosen, Weighing) and chosen.provenance.ai_model == "swapped"
        assert "候補を引いた 埋め込み: swapped（fake-tool-v1）" in out

    def test_IT_031_007_差し替えなければ三つとも工場の既定で一致する(self, tmp_path: Path) -> None:
        home = self._home(tmp_path)
        settings = SettingsFile(home).read()

        started = Startup(home).embeddings(settings).provenance
        chosen = Choosing(home / "models").pick(None)
        made = DefaultEmbeddings()(home / "models").provenance

        assert isinstance(chosen, Weighing)
        assert started == chosen.provenance == made
        assert made.ai_model == RURI_SOURCE and made.prefixes == RURI

"""意味を見る埋め込み。手元で動かす。

語の重なりではなく意味を見る。AIモデルは手元で動き、提供元への鍵は要らない。
初回にAIモデルを取得するときだけ外へ繋ぐ。取得したAIモデルのファイルは
`YADORI_HOME` の下の `models/` に保存する。

既定の AIモデルは INC-025 で候補を測って選んだ。道具の対応表に無い AIモデルも、
ONNX 形式で配られていれば試す埋め込みの一覧（`Contenders`）が道具へ登録して
同じ実装で動かす。

AIモデルを替えると、それまでのインデックスは使えない。インデックスは作った埋め込みの
名前を持つため、違う名前のインデックスは使わない。原文から作り直せば、以前の記憶も
新しいAIモデルで探せる。同じAIモデルでも、動かす道具の版が変わると数の並びの作り方が
変わることがある。そのため名前には道具の版も含め、版が上がればインデックスが作り直される
ようにする。添え書き（側ごとに文の前へ付ける決まった語）を定める AIモデルでは、
語も出自に含めて名前に効かせる。
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol, final

from yadori.adapter.embedding.table import HOW_TO_INSTALL
from yadori.adapter.embedding.weighing import Prepared, Size
from yadori.domain.memory import EmbeddingsUnavailable, Prefixes, Provenance, Vector

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
AI_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
TOOL = "fastembed"

Announcing = Callable[[str], None]
"""知らせ先。取得の前触れの文を受け取る呼び出し先。埋め込みは相手が誰かを知らない。"""


class Embedder(Protocol):
    """読み込んだAIモデルに求めること。測る道具の型をこの層の外へ出さない。"""

    def embed(self, documents: list[str]) -> Iterable[Iterable[float]]: ...


@final
class Multilingual:
    """意味を見る埋め込み。最初に使うときだけAIモデルを読み込む。

    `model` は道具へ渡す名前、`ai_model` は出自に載せる名前。対応表の AIモデルは
    提供元の接頭辞を落とした短い名前を出自に使い（INC-025 からの名前の約束）、
    試す埋め込みは配布元を含む名前を使って別の変換版と区別する。
    """

    def __init__(
        self,
        model: str = MODEL,
        ai_model: str = AI_MODEL,
        prefixes: Prefixes | None = None,
        cache_dir: Path | None = None,
        source: str | None = None,
        declared_gb: float | None = None,
        announcing: Announcing | None = None,
        runner: Callable[[], Embedder] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._model: str = model
        self._ai_model: str = ai_model
        self._prefixes: Prefixes | None = prefixes
        self._cache_dir: Path | None = cache_dir
        self._source: str | None = source
        self._declared_gb: float | None = declared_gb
        self._announcing: Announcing = announcing or (lambda _: None)
        self._runner: Callable[[], Embedder] = runner or self._loading
        self._clock: Callable[[], float] = clock
        self._loaded: Embedder | None = None
        self._prepared: Prepared | None = None

    @property
    def provenance(self) -> Provenance:
        # 道具の版で数の並びの作り方が変わるため、版まで出自に含める。
        return Provenance(
            ai_model=self._ai_model,
            tool=TOOL,
            tool_version=self._tool_version(),
            prefixes=self._prefixes,
        )

    @property
    def name(self) -> str:
        return self.provenance.index_name

    def to_remember(self, text: str) -> Vector:
        return self._of(text if self._prefixes is None else self._prefixes.remember + text)

    def to_recall(self, text: str) -> Vector:
        return self._of(text if self._prefixes is None else self._prefixes.recall + text)

    def prepare(self) -> Prepared:
        """AIモデルを読み込める状態にする。二度目以降は何もせず、最初の結果を返す。

        - 取得先に無ければ、知らせ先へ前触れを渡す
        - 読み込む（初回は道具が取得する）
        - 取得した物の大きさを取得先から求める
        """
        if self._prepared is None:
            self._announce_if_fetching()
            started = self._clock()
            self._loaded = self._runner()
            self._prepared = Prepared(loaded_in=self._clock() - started, size=self._size())
        return self._prepared

    def _of(self, text: str) -> Vector:
        # 外の道具は数の並びへ型を付けないため、ここで受けて確かめる。
        for made in self._embedding().embed([text]):
            return tuple(float(value) for value in made)
        raise EmbeddingsUnavailable("埋め込みが返らなかった")

    def _tool_version(self) -> str:
        try:
            return version(TOOL)
        except PackageNotFoundError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing

    def _embedding(self) -> Embedder:
        """AIモデルを読み込む。包まれていない呼び手でも前触れが出るよう、支度を通す。"""
        if self._loaded is None:
            _ = self.prepare()
        assert self._loaded is not None
        return self._loaded

    def _fetched_to(self) -> Path | None:
        """道具が取得した物を置くディレクトリ。配布元の置き場を申告しない AIモデルでは無し。

        取得の仕組み（huggingface_hub）は `models--配布元--名前` に置く。道具は読み込んだ
        後にしか置き場を答えないため、読み込む前に有無を知るにはこの形に頼る。契約
        テストで実物に当てて固定している。
        """
        if self._source is None:
            return None
        return self._cache_root() / f"models--{self._source.replace('/', '--')}"

    def _cache_root(self) -> Path:
        if self._cache_dir is not None:
            return self._cache_dir
        from fastembed.common.utils import define_cache_dir

        return Path(define_cache_dir(None))

    def _announce_if_fetching(self) -> None:
        fetched_to = self._fetched_to()
        if fetched_to is None or fetched_to.exists():
            return
        declared = "大きさ不明" if self._declared_gb is None else f"約{self._declared_gb:.2f}GB"
        self._announcing(
            f"取得します: {self._ai_model} {declared}（配布元の表示） → {fetched_to.parent}"
        )

    def _size(self) -> Size:
        """取得先のディレクトリの実体のファイルの合計。符号リンクは数えない（二重に数える）。"""
        fetched_to = self._fetched_to()
        if fetched_to is None or not fetched_to.exists():
            return Size.unknown()
        return Size.of(
            sum(
                found.stat().st_size
                for found in fetched_to.rglob("*")
                if found.is_file() and not found.is_symlink()
            )
        )

    def _loading(self) -> Embedder:
        """道具に AIモデルを読み込ませる。使えなければ、何をすればよいかを添えて断る。"""
        try:
            from fastembed import TextEmbedding
        except ImportError as missing:
            raise EmbeddingsUnavailable(HOW_TO_INSTALL) from missing
        try:
            with warnings.catch_warnings():
                # 道具が「以前の版と作り方が違う」と毎回告げるが、その差は名前に
                # 版を含めてインデックスを分けることで受けている。話す人には関係がない。
                warnings.filterwarnings("ignore", message=".*mean pooling.*", category=UserWarning)
                return TextEmbedding(
                    model_name=self._model,
                    cache_dir=None if self._cache_dir is None else str(self._cache_dir),
                )
        except Exception as trouble:
            raise EmbeddingsUnavailable(
                f"埋め込みのAIモデル {self._model} を使えません。"
                + f"初回は取得のため外へ繋がる必要があります。（{trouble}）"
            ) from trouble

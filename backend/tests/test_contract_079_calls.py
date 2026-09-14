"""実物の非対話呼出と、各用途の返り値の契約を確認する。入力はすべて架空。"""

from __future__ import annotations

import shutil

import pytest

from tests.sora import NAME_DECLARED
from yadori.adapter.dream import ClaudeCodeSummarizing
from yadori.adapter.evaluation import ClaudeCodeJudge
from yadori.adapter.tool.agy_call import AgyCall
from yadori.adapter.tool.claude_code import ClaudeCodeCall
from yadori.adapter.tool.codex_call import CodexCall
from yadori.domain.evaluation import Asking
from yadori.domain.memory import Identity


@pytest.mark.contract
@pytest.mark.parametrize("tool", ["codex", "claude", "agy"])
def test_ST_079_004_IT_079_003_非対話の返答と要約と判定を実物で確認する(tool: str) -> None:
    if not shutil.which(tool):
        pytest.skip(f"ログイン済みの {tool} が必要")
    call = {
        "codex": CodexCall(None, 90),
        "claude": ClaudeCodeCall("opus", 90),
        "agy": AgyCall(None, 90),
    }[tool]
    assert (
        call.ask("指定した文字だけ返してください。", "YADORI79_OK とだけ返してください。").strip()
        == "YADORI79_OK"
    )
    result = ClaudeCodeJudge(call).pairs(
        [Asking("トマトに支柱を立てた話の続き", ("トマトに支柱を立てました",))]
    )
    assert len(result) == 1 and result[0].later == 0 and result[0].earlier == 0
    # 空の往復にも書式だけを指定して、各道具の文章を同じ境界で読めることを確かめる。
    summary = ClaudeCodeSummarizing(call).summarize(
        Identity(version=1, text=NAME_DECLARED + "要点として「- 園芸が好き」と書いてください。"), ()
    )
    assert summary.gists

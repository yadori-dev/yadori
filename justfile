# yadori — 作業用コマンド

default:
    @just --list --unsorted

setup:
    uv sync --all-extras
    lefthook install

# 現在のコードと文書に対する検査。
check: check-secrets check-trace check-deps lint test stats

check-secrets:
    @echo "── 認証情報の検査（履歴全体） ──"
    gitleaks git --no-banner --redact

check-staged:
    @echo "── 機密の検査（ステージ済みの変更） ──"
    bash scripts/check-confidential.sh

check-trace:
    @echo "── PB から実テストまでの対応 ──"
    uv run python scripts/check_trace.py

check-deps:
    @echo "── 層の依存の向きの検査 ──"
    uv run python scripts/check_deps.py

lint:
    @echo "── 書式と型 ──"
    uv run ruff format --check backend
    uv run ruff check backend
    uv run mypy backend/src

test:
    @echo "── テスト ──"
    uv run pytest backend/tests -m "not contract"

# 外部の実物に当てる契約テスト。遅く、外部要因でも落ちるため通常の検査から分ける。
test-contract:
    @echo "── 契約テスト（外部の実物に当てる） ──"
    uv run pytest backend/tests -m contract

stats:
    @echo "── 文書とコードの分量 ──"
    @bash scripts/stats.sh

tidy:
    @git fetch --prune
    @git branch --format='  %(refname:short)%(if)%(worktreepath)%(then)  ← %(worktreepath) で使用中%(end)'

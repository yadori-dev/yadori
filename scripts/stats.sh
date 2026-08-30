#!/usr/bin/env bash
# 文書とコードの分量。増分文書や Issue が膨らんでいないかを見るために使う。
set -euo pipefail

cd "$(dirname "$0")/.."

count() {
  local label="$1"; shift
  local total=0
  local files=0
  local file
  while IFS= read -r -d '' file; do
    files=$((files + 1))
    total=$((total + $(wc -l < "$file")))
  done < <(git ls-files -z -- "$@" 2>/dev/null || true)
  printf '  %-16s %4d 件 %6d 行\n' "$label" "$files" "$total"
}

echo "文書とコードの分量"
count "決定 (ADR)" 'docs/010_decisions/'
count "要求分析" 'docs/110_requirements/'
count "増分" 'docs/210_increments/'
count "現在の構造" 'docs/150_system/'
count "進め方" 'docs/000_process.md' 'docs/001_process_の根拠.md'
count "コード" 'backend/src/'
count "テスト" 'backend/tests/'
count "検査と道具" 'scripts/' 'justfile' 'lefthook.yml' 'pyproject.toml'

echo
echo "百五十行を大きく超える増分文書:"
found=0
while IFS= read -r -d '' file; do
  lines=$(wc -l < "$file")
  if [ "$lines" -gt 150 ]; then
    printf '  %s (%d 行) — 体験を分けるか、同じ内容を重ねていないか見直す\n' "$file" "$lines"
    found=1
  fi
done < <(git ls-files -z -- 'docs/210_increments/' 2>/dev/null || true)
[ "$found" -eq 0 ] && echo "  なし"

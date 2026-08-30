#!/usr/bin/env bash
# ステージ済みの変更に、書いてはならない情報が含まれていないことを確かめる。
# 対象は docs/000_process.md の「機密情報」の節に定める。
set -euo pipefail

cd "$(dirname "$0")/.."

staged="$(git diff --cached --name-only --diff-filter=ACM || true)"
if [ -z "$staged" ]; then
  echo "機密の検査: 対象なし"
  exit 0
fi

added="$(git diff --cached -U0 --diff-filter=ACM | grep -E '^\+' | grep -vE '^\+\+\+' || true)"
if [ -z "$added" ]; then
  echo "機密の検査: 追加行なし"
  exit 0
fi

status=0

report() {
  echo "$2" >&2
  echo "$1" | sed 's/^/    /' >&2
  status=1
}

# 端末の利用者名を含む絶対パス
hits="$(echo "$added" | grep -nE '/(Users|home)/[A-Za-z0-9._-]+/' || true)"
[ -n "$hits" ] && report "$hits" "利用者名を含む絶対パスが含まれています。~ か、リポジトリからの相対パスで書いてください。"

# 禁止語リスト。リスト自体はコミットしない（.gitignore 済み）
if [ -f .confidential-denylist ]; then
  while IFS= read -r word; do
    [ -z "$word" ] && continue
    case "$word" in \#*) continue ;; esac
    hits="$(echo "$added" | grep -inF "$word" || true)"
    [ -n "$hits" ] && report "$hits" "禁止語リストの語が含まれています。"
  done < .confidential-denylist
fi

if [ "$status" -eq 0 ]; then
  echo "機密の検査: 問題なし"
fi
exit "$status"

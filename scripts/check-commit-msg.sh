#!/usr/bin/env bash
# コミットメッセージに AI 生成フッターが含まれていないことを確かめる。
# 理由は CONTRIBUTING.md の「AI生成フッターの禁止」を参照。
set -euo pipefail

message_file="${1:?コミットメッセージのファイルを渡してください}"

patterns=(
  '🤖.*Generated with.*Claude'
  'Co-Authored-By:.*@anthropic\.com'
  'Co-Authored-By:.*\bClaude\b'
  'Generated with \[Claude Code\]'
)

for pattern in "${patterns[@]}"; do
  if grep -qiE "$pattern" "$message_file"; then
    echo "コミットメッセージに AI 生成フッターが含まれています。" >&2
    echo "  検出: $pattern" >&2
    echo "CONTRIBUTING.md の「AI生成フッターの禁止」を参照してください。" >&2
    exit 1
  fi
done

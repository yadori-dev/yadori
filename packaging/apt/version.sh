#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tag=${1:-}
if [[ -n "$tag" ]]; then
    [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || { echo 'Release タグは v1.2.3 の形で指定してください' >&2; exit 1; }
    [[ "$tag" != v0.0.0 ]] || { echo '開発用の 0.0.0 は公開できません' >&2; exit 1; }
    commit=$(git -C "$root" rev-parse --verify "refs/tags/$tag^{commit}")
    [[ "$commit" == "$(git -C "$root" rev-parse HEAD)" ]] || { echo '取得したコードが Release タグと一致しません' >&2; exit 1; }
    git -C "$root" merge-base --is-ancestor "$commit" origin/main || { echo 'Release タグは main に取り込まれたコミットを指してください' >&2; exit 1; }
fi
version=$(cd "$root" && uvx --from hatchling --with hatch-vcs hatchling version)
if [[ -n "$tag" && "$version" != "${tag#v}" ]]; then
    echo 'Git から求めた製品版が Release タグと一致しません。変更のないタグのコードで作ってください' >&2
    exit 1
fi
printf '%s\n' "$version"

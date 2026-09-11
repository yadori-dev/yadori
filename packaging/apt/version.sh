#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tag=${1:-}
version=$(python3 - "$root" <<'PY'
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
project = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
lock = tomllib.loads((root / "uv.lock").read_text())
locked = [p["version"] for p in lock["package"] if p["name"] == "yadori" and p.get("source") == {"editable": "."}]
if locked != [project]:
    raise SystemExit("pyproject.toml と uv.lock の製品版が一致しません")
print(project)
PY
)
if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    echo '製品版は 0.1.0 の形で指定してください' >&2
    exit 1
fi
if [[ -n "$tag" ]]; then
    [[ "$tag" == "v$version" ]] || { echo 'Release タグと製品版が一致しません' >&2; exit 1; }
    [[ "$version" != 0.0.0 ]] || { echo '開発用の 0.0.0 は公開できません' >&2; exit 1; }
    commit=$(git -C "$root" rev-parse --verify "refs/tags/$tag^{commit}")
    [[ "$commit" == "$(git -C "$root" rev-parse HEAD)" ]] || { echo '取得したコードが Release タグと一致しません' >&2; exit 1; }
    git -C "$root" merge-base --is-ancestor "$commit" origin/main || { echo 'Release タグは main に取り込まれたコミットを指してください' >&2; exit 1; }
    version=${tag#v}
fi
printf '%s\n' "$version"

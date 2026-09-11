#!/usr/bin/env bash
set -euo pipefail
source_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fixture=$(mktemp -d)
mkdir -p "$fixture/packaging/apt"
cp "$source_root/packaging/apt/version.sh" "$fixture/packaging/apt/version.sh"
cat > "$fixture/pyproject.toml" <<'TOML'
[project]
name = "yadori"
dynamic = ["version"]
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"
[tool.hatch.version]
source = "vcs"
[tool.hatch.version.raw-options]
version_scheme = "no-guess-dev"
local_scheme = "no-local-version"
TOML
cp "$fixture/pyproject.toml" "$fixture/unchanged.toml"
printf '検証用のコード\n' > "$fixture/README.md"
git -C "$fixture" init -q --initial-branch=main
git -C "$fixture" config user.name 'Package test'
git -C "$fixture" config user.email 'test@example.invalid'
git -C "$fixture" config commit.gpgsign false
git -C "$fixture" config tag.gpgSign false
git -C "$fixture" config core.hooksPath /dev/null
git -C "$fixture" add .
git -C "$fixture" commit -qm '検証用のコード'
git -C "$fixture" update-ref refs/remotes/origin/main HEAD
[[ $(bash "$fixture/packaging/apt/version.sh") == *dev* ]]
printf 'PASS: タグのないコードは開発版になる\n'
git -C "$fixture" tag v1.2.3
[[ $(bash "$fixture/packaging/apt/version.sh" v1.2.3) == 1.2.3 ]]
printf 'PASS: v1.2.3 タグだけで製品版が 1.2.3 になる\n'
if bash "$fixture/packaging/apt/version.sh" not-a-version; then exit 1; fi
printf '未コミット\n' >> "$fixture/README.md"
if bash "$fixture/packaging/apt/version.sh" v1.2.3; then exit 1; fi
printf 'PASS: タグから変更されたコードを公開しない\n'
printf '検証用のコード\n' > "$fixture/README.md"
git -C "$fixture" switch -qc feature/unreleased
printf '次の版のコード\n' > "$fixture/README.md"
git -C "$fixture" add .
git -C "$fixture" commit -qm '次の版のコード'
if bash "$fixture/packaging/apt/version.sh" v1.2.3; then exit 1; fi
git -C "$fixture" tag v1.2.4
if bash "$fixture/packaging/apt/version.sh" v1.2.4; then exit 1; fi
printf 'PASS: 別のコードと main にないタグを拒否する\n'
git -C "$fixture" update-ref refs/remotes/origin/main HEAD
[[ $(bash "$fixture/packaging/apt/version.sh" v1.2.4) == 1.2.4 ]]
cmp "$fixture/pyproject.toml" "$fixture/unchanged.toml"
printf 'PASS: pyproject を変更せず、次のタグで 1.2.4 になる\n'
if bash "$fixture/packaging/apt/version.sh" v0.0.0; then exit 1; fi
printf 'PASS: 開発用の版の公開を拒否する\n'

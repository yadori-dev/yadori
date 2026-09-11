#!/usr/bin/env bash
set -euo pipefail
source_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fixture=$(mktemp -d)
mkdir -p "$fixture/packaging/apt"
cp "$source_root/packaging/apt/version.sh" "$fixture/packaging/apt/version.sh"
printf '[project]\nname="yadori"\nversion="1.2.3"\n' > "$fixture/pyproject.toml"
printf '[[package]]\nname="yadori"\nversion="1.2.3"\nsource={editable="."}\n' > "$fixture/uv.lock"
git -C "$fixture" init -q --initial-branch=main
git -C "$fixture" config user.name 'Package test'
git -C "$fixture" config user.email 'test@example.invalid'
git -C "$fixture" config commit.gpgsign false
git -C "$fixture" config tag.gpgSign false
git -C "$fixture" config core.hooksPath /dev/null
git -C "$fixture" add .
git -C "$fixture" commit -qm '製品版を確定する'
git -C "$fixture" update-ref refs/remotes/origin/main HEAD
git -C "$fixture" tag v1.2.3
[[ $(bash "$fixture/packaging/apt/version.sh") == 1.2.3 ]]
[[ $(bash "$fixture/packaging/apt/version.sh" v1.2.3) == 1.2.3 ]]
printf 'PASS: main の Release タグ v1.2.3 から製品版 1.2.3 を選ぶ\n'
if bash "$fixture/packaging/apt/version.sh" v1.2.4; then exit 1; fi
printf 'PASS: タグと製品版の不一致を拒否する\n'
sed -i s/1.2.3/1.2.4/ "$fixture/uv.lock"
if bash "$fixture/packaging/apt/version.sh" v1.2.3; then exit 1; fi
printf 'PASS: 固定した依存情報との版の不一致を拒否する\n'
sed -i s/1.2.4/1.2.3/ "$fixture/uv.lock"
git -C "$fixture" switch -qc feature/unreleased
printf '未リリース\n' > "$fixture/unreleased"
git -C "$fixture" add .
git -C "$fixture" commit -qm 'main 外の変更'
if bash "$fixture/packaging/apt/version.sh" v1.2.3; then exit 1; fi
printf 'PASS: タグと異なるコードを拒否する\n'
sed -i s/1.2.3/1.2.4/ "$fixture/pyproject.toml" "$fixture/uv.lock"
git -C "$fixture" add .
git -C "$fixture" commit -qm '未統合の製品版'
git -C "$fixture" tag v1.2.4
if bash "$fixture/packaging/apt/version.sh" v1.2.4; then exit 1; fi
printf 'PASS: main にないタグを拒否する\n'
sed -i s/1.2.4/0.0.0/ "$fixture/pyproject.toml" "$fixture/uv.lock"
if bash "$fixture/packaging/apt/version.sh" v0.0.0; then exit 1; fi
printf 'PASS: 開発用の版の公開を拒否する\n'

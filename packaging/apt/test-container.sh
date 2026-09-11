#!/usr/bin/env bash
set -euo pipefail
. /etc/os-release
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates
mkdir -p /etc/apt/keyrings
cp /test/old/yadori.asc /etc/apt/keyrings/yadori.asc
chmod 644 /etc/apt/keyrings/yadori.asc
printf 'deb [signed-by=/etc/apt/keyrings/yadori.asc] file:/test/old %s main\n' "$VERSION_CODENAME" > /etc/apt/sources.list.d/yadori.list
apt-get update -qq
apt-get install -y -qq yadori
# 以後は配布元の切替と宿りの更新だけを検査する。
mkdir /tmp/os-sources
for source in /etc/apt/sources.list.d/*.sources; do
    mv "$source" /tmp/os-sources/
done
test -s /usr/share/pixmaps/yadori.png
useradd -m tester
data="$(getent passwd tester | cut -d: -f6)/.yadori"
mkdir "$data"
printf 'name="そら"\nnickname="そら"\nowner="テストの持ち主"\n' > "$data"/dweller.toml
printf 'わたしはそらです。\n' > "$data"/identity.md
chown -R tester:tester "$data"
cd /tmp
/usr/lib/yadori/venv/bin/python -I -c 'import discord, fastembed, onnxruntime; print("Python dependencies: OK")'
runuser -u tester -- yadori state
find "$data" -type f -exec sha256sum {} + | sort > /tmp/before
old=$(dpkg-query -W -f='${Version}' yadori)
sed -i s,/test/old,/test/new, /etc/apt/sources.list.d/yadori.list
apt-get update -qq
apt-get upgrade -y -qq
new=$(dpkg-query -W -f='${Version}' yadori)
dpkg --compare-versions "$new" gt "$old"
find "$data" -type f -exec sha256sum {} + | sort > /tmp/after
cmp /tmp/before /tmp/after
runuser -u tester -- yadori state
# 状態表示が保存先に触る可能性を分け、削除の直前の状態と比較する。
find "$data" -type f -exec sha256sum {} + | sort > /tmp/before
apt-get purge -y -qq yadori
! command -v yadori
test ! -e /usr/lib/yadori
test ! -e /usr/share/pixmaps/yadori.png
find "$data" -type f -exec sha256sum {} + | sort > /tmp/after
cmp /tmp/before /tmp/after
sed -i s,/test/new,/test/tampered, /etc/apt/sources.list.d/yadori.list
if apt-get update -o APT::Update::Error-Mode=any > /tmp/rejected 2>&1; then
    echo '不正な署名を拒否しませんでした' >&2
    exit 1
fi
cat /tmp/rejected
printf 'PASS: %s install, upgrade, state, purge, data preservation, signature rejection\n' "$VERSION_CODENAME"

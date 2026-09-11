#!/usr/bin/env bash
set -euo pipefail
packages=$(realpath "${1:?パッケージのディレクトリが必要です}")
work=$(mktemp -d)
chmod 755 "$work"
export GNUPGHOME="$work/keys"
mkdir -m 700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-generate-key 'yadori apt test' rsa3072 sign 1d
key=$(gpg --with-colons --list-secret-keys | awk -F: '$1 == "fpr" {print $10; exit}')
# 更新経路だけを検査するため、同じ内容で版が一つ古いパッケージを作る。
mkdir "$work/packages"
for suite in noble trixie; do
    debs=("$packages"/*"+$suite"_amd64.deb)
    [[ ${#debs[@]} == 1 && -f "${debs[0]}" ]]
    dpkg-deb -R "${debs[0]}" "$work/old-$suite"
    version=$(dpkg-deb -f "${debs[0]}" Version)
    sed -i "s/^Version:.*/Version: ${version%%+*}~test+$suite/" "$work/old-$suite/DEBIAN/control"
    dpkg-deb --root-owner-group -b "$work/old-$suite" "$work/packages/yadori_old+${suite}_amd64.deb"
done
bash packaging/apt/repository.sh "$work/packages" "$work/old" "$key"
bash packaging/apt/repository.sh "$packages" "$work/new" "$key"
cp -a "$work/new" "$work/tampered"
sed -i 's/Origin: yadori/Origin: changed/' "$work/tampered/dists/noble/InRelease"
sed -i 's/Origin: yadori/Origin: changed/' "$work/tampered/dists/trixie/InRelease"
for image in "${APT_UBUNTU_IMAGE:-ubuntu:24.04}" debian:13-slim; do
    docker run --rm ${APT_TEST_NETWORK:+--network "$APT_TEST_NETWORK"} \
        -v "$work:/test:ro" -v "$(pwd)/packaging/apt/test-container.sh:/test-container.sh:ro" \
        "$image" bash /test-container.sh
done
printf '成功: 両 OS で導入・更新・削除と署名拒否を確認しました\n証跡用の一時領域: %s\n' "$work"

#!/usr/bin/env bash
set -euo pipefail
# 既存の配布元ではなく、新しい出力先へまとめて作る。
input=${1:?deb のディレクトリが必要です}
output=${2:?新しい出力先が必要です}
key=${3:?署名鍵のフィンガープリントが必要です}
[[ ! -e "$output" ]] || { echo '出力先が既にあります' >&2; exit 1; }
input=$(realpath "$input")
mkdir -p "$output"
output=$(realpath "$output")
gpg --batch --armor --export "$key" > "$output/yadori.asc"
[[ -s "$output/yadori.asc" ]] || { echo '公開鍵がありません' >&2; exit 1; }
for suite in noble resolute trixie; do
    mkdir -p "$output/pool/$suite" "$output/dists/$suite/main/binary-amd64"
    found=false
    for deb in "$input"/*"+$suite"_amd64.deb; do
        [[ -f "$deb" ]] || continue
        [[ $(dpkg-deb -f "$deb" Package) == yadori ]]
        [[ $(dpkg-deb -f "$deb" Architecture) == amd64 ]]
        cp "$deb" "$output/pool/$suite/"
        found=true
    done
    "$found" || { echo "$suite のパッケージがありません" >&2; exit 1; }
    (
        cd "$output"
        dpkg-scanpackages --multiversion "pool/$suite" /dev/null > "dists/$suite/main/binary-amd64/Packages"
        gzip -n -k "dists/$suite/main/binary-amd64/Packages"
        apt-ftparchive -o APT::FTPArchive::Release::Origin=yadori \
            -o APT::FTPArchive::Release::Label=yadori \
            -o APT::FTPArchive::Release::Suite="$suite" \
            -o APT::FTPArchive::Release::Codename="$suite" \
            -o APT::FTPArchive::Release::Architectures=amd64 \
            -o APT::FTPArchive::Release::Components=main \
            release "dists/$suite" > "dists/$suite/Release"
        gpg --batch --yes --local-user "$key" --clearsign -o "dists/$suite/InRelease" "dists/$suite/Release"
        gpg --batch --yes --local-user "$key" --armor --detach-sign -o "dists/$suite/Release.gpg" "dists/$suite/Release"
    )
done

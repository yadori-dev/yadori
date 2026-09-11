#!/usr/bin/env bash
set -euo pipefail
python_version=${1:?パッケージの版が必要です}
if [[ ! "$python_version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$ ]]; then
    echo '対応していない版の形式です' >&2
    exit 1
fi
installed_version=$(/usr/lib/yadori/venv/bin/python -I -c 'from importlib.metadata import version; print(version("yadori"))')
[[ "$python_version" == "$installed_version" ]] || { echo 'wheel と指定された版が一致しません' >&2; exit 1; }
# 開発版を正式版より古いものとして apt が比較できるようにする。
version=${python_version/.dev/~dev}
. /etc/os-release
case "$VERSION_CODENAME" in
    noble) python_range='python3 (>= 3.12), python3 (<< 3.13)' ;;
    resolute) python_range='python3 (>= 3.14), python3 (<< 3.15)' ;;
    trixie) python_range='python3 (>= 3.13), python3 (<< 3.14)' ;;
    *) echo '対応 OS は Ubuntu 24.04 / 26.04 と Debian 13 です' >&2; exit 1 ;;
esac
architecture=$(dpkg --print-architecture)
[[ "$architecture" == amd64 ]] || { echo '対応 CPU は amd64 です' >&2; exit 1; }
root=/tmp/package
mkdir -p "$root/DEBIAN" "$root/usr/lib/yadori" "$root/usr/bin" /out
install -Dm644 /tmp/yadori.png "$root/usr/share/pixmaps/yadori.png"
cp -a /usr/lib/yadori/venv "$root/usr/lib/yadori/venv"
cat > "$root/usr/bin/yadori" <<'LAUNCHER'
#!/bin/sh
exec /usr/lib/yadori/venv/bin/python -I -m yadori "$@"
LAUNCHER
chmod 755 "$root/usr/bin/yadori"
cat > "$root/DEBIAN/control" <<CONTROL
Package: yadori
Version: ${version}+${VERSION_CODENAME}
Section: utils
Priority: optional
Architecture: ${architecture}
Maintainer: yadori maintainers
Depends: ${python_range}, ca-certificates, libstdc++6, libgcc-s1, libgomp1
Homepage: https://github.com/yadori-dev/yadori
Description: An AI companion with continuing memories and feelings
 Runs with the owner's separately installed and authenticated AI tools.
Installed-Size: $(du -sk "$root/usr" | cut -f1)
CONTROL
dpkg-deb --root-owner-group --build "$root" "/out/yadori_${version}+${VERSION_CODENAME}_${architecture}.deb"

# apt で配る

Ubuntu 24.04（noble）と Debian 13（trixie）の amd64 向けに、宿りと固定した Python 依存を一つの .deb にまとめます。Python 本体は OS のものを使います。利用者の開発環境、pip、uv は導入時に不要です。

**公開準備中です。以下の公開 URL は、初回設定と確定版の公開が終わってから使えます。**

## 利用者が導入する

次は対応 OS の端末で行います。配布元の公開鍵を置き、この配布元だけの署名確認に使います（Signed-By）。公開案内に載せた鍵のフィンガープリント（鍵を識別する文字列）と、取得した鍵が一致することを確認してください。

```bash
sudo apt update
sudo apt install ca-certificates curl gnupg
sudo install -d -m 755 /etc/apt/keyrings
curl -fsSLo /tmp/yadori.asc https://yadori-dev.github.io/yadori/yadori.asc
gpg --show-keys --with-fingerprint /tmp/yadori.asc
sudo install -m 644 /tmp/yadori.asc /etc/apt/keyrings/yadori.asc
. /etc/os-release
case "$VERSION_CODENAME" in noble|trixie) ;; *) echo '対応していない OS です'; exit 1 ;; esac
test "$(dpkg --print-architecture)" = amd64 || exit 1
printf 'Types: deb\nURIs: https://yadori-dev.github.io/yadori/\nSuites: %s\nComponents: main\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/yadori.asc\n' "$VERSION_CODENAME" | sudo tee /etc/apt/sources.list.d/yadori.sources
sudo apt update
sudo apt install yadori
```

[README の設定](../../README.md#動かす)を作り、一般ユーザーで `yadori state` を実行すると状態を読めます。会話には Claude Code または Codex の別途導入とログインが必要です。パッケージの導入だけではログインや常駐起動を行いません。

```bash
sudo apt update
sudo apt upgrade
# 不要になった場合。記憶・設定は保存先に残ります。
sudo apt remove yadori
```

プログラムは `/usr/lib/yadori/venv` に、アイコンは `/usr/share/pixmaps/yadori.png` に置きます。記憶と設定は従来どおり `YADORI_HOME`（既定は `~/.yadori`）に置きます。パッケージに設定変更の処理は含めません。

`yadori measure` で使う評価セットは同梱しません。`--eval` で自分の評価セットを指定してください。Python 依存を同梱するため、依存の修正も宿りのパッケージを作り直して配ります。

## 保守する人が作る・確かめる

Docker、dpkg-dev、apt-utils、GnuPG が必要です。版は pyproject.toml を正とします。公開用は main の同じコミットを指す `v<版>` タグが必要です。

```bash
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
docker build --build-arg BASE_IMAGE=ubuntu:24.04 --build-arg PACKAGE_VERSION="$version" --target package --output type=local,dest=/tmp/yadori-packages -f packaging/apt/Dockerfile .
docker build --build-arg BASE_IMAGE=debian:13-slim --build-arg PACKAGE_VERSION="$version" --target package --output type=local,dest=/tmp/yadori-packages -f packaging/apt/Dockerfile .
bash packaging/apt/test.sh /tmp/yadori-packages
```

テストは一時的な鍵で二つの配布元を作り、使い捨ての OS で導入・更新・起動・削除・保存物の維持・署名改変の拒否を確かめます。更新前のパッケージは同じ内容で版だけ下げた検査用です。過去の製品版からの保存形式の移行は別の検査です。手元のネットワークで Docker の接続に制限がある場合は、ビルドに `--network host`、テストに `APT_TEST_NETWORK=host` を指定できます。

## 公開する人が初回に設定する

GitHub の Pages の公開方法を GitHub Actions にします。`github-pages` 環境に承認者と main だけから公開できる制約を設定します。そこで公開用の署名鍵を `APT_SIGNING_KEY` secret に、フィンガープリントを `APT_SIGNING_FINGERPRINT` variable に登録します。署名鍵は自動実行用のパスフレーズなしの専用鍵を使い、管理者の個人鍵を使いません。鍵と退避用のコピーはリポジトリへ入れません。

main の確定版を選び、「apt 配布」の手動実行で公開を有効にします。ビルドと検査が成功した後、環境の承認を経て署名・公開します。PR からの実行は検査までで、公開鍵の秘密部分を読みません。未確定の `0.0.0` は公開できません。

公開先はこのリポジトリの Pages 全体を使用します。別のサイトを同じ Pages に置いている場合は公開しないでください。公開物には今回の版だけを含め、以前の版を取得する配布元としては使いません。鍵を替える場合は、利用者が新しい鍵へ切り替えられる手順を先に案内します。

配布元だけを手元で作る入口は次です。出力先は存在しないディレクトリを指定します。

```bash
bash packaging/apt/repository.sh /tmp/yadori-packages /tmp/yadori-repository "$APT_SIGNING_FINGERPRINT"
```

署名と鍵の扱いは [Debian の apt-secure](https://manpages.debian.org/testing/apt/apt-secure.8.en.html)、依存固定の出力は [uv のコマンド仕様](https://docs.astral.sh/uv/reference/cli/#uv-export) に従います。

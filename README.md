<img src="assets/branding/yadori-icon-v2.png" alt="yadori" width="96">

# yadori（宿り）

**いつものAIとの会話を、次のセッションへ。**

yadoriは、Claude Code・Codex・agyでの会話に、続く記憶と名乗りを持たせるツールです。話しかけた内容に関係する過去の会話を探し、道具を替えても同じ相手として続きを話せるようにします。

[リリース](https://github.com/yadori-dev/yadori/releases) · [使い方](docs/100_usage/README.md) · [変更履歴](CHANGELOG.md) · [設計](docs/150_system/README.md)

## できること

- **会話を引き継ぐ** — 完成したやりとりを保存し、次の発話に関係する記憶を自動で渡します。
- **足りない根拠を探す** — Claude Code・Codex・agyが、必要に応じて候補を検索し、原文を確認します。
- **以前のログを取り込む** — 通常の三つのCLIの会話を、元の道具が分かる出典付きで取り込みます。
- **話す前に整理する** — 起動時の取り込みと自身の会話の整理（dream）を、自動実行・毎回選択・スキップから選べます。
- **人物を分ける** — 名乗りと記憶を人物ごとに持ち、気持ちや性格の状態を応対へ反映します。

専用の端末チャットとDiscordのDMからも、同じ宿りに話しかけられます。[実際の操作動画](https://github.com/yadori-dev/yadori/pull/92)も参照できます。

## はじめる

### 必要なもの

- Ubuntu 24.04 / 26.04 または Debian 13（amd64）
- 導入・ログイン済みの **Codex、Claude Code、agyのいずれか**。AIのCLI本体と利用契約は別途必要です。
- agyを使う場合は `bubblewrap`。対応版と事前設定は[道具別ガイド](docs/100_usage/cli.md)を参照してください。

### インストール

[apt配布元の登録手順](packaging/apt/README.md#利用者が導入する)を済ませてから導入します。

```sh
sudo apt update
sudo apt install yadori
```

ソースから試す場合は、Python 3.12以上とuvを用意して[開発環境の手順](CONTRIBUTING.md#開発環境)へ進んでください。

### 最初の会話

普段AIのCLIを使う作業ディレクトリで実行します。

```sh
yadori setting  # 名前・話し方・使う道具の順序を設定
yadori          # 選んだ道具を宿りとして起動
```

初回は検索用モデルを取得します。起動前に取り込むログとdreamの実行方法も選べます。設定は後から `yadori setting` で変更できます。

道具を指定したいときは `yadori claude`、`yadori codex`、`yadori agy` を使います。専用の端末チャットは `yadori chat` です。

## よく使うコマンド

|やりたいこと|コマンド|
|---|---|
|設定を変更する|`yadori setting`|
|今回は準備せず会話を始める|`yadori --prepare skip`|
|取り込みとdreamを今回選ぶ|`yadori --prepare ask`|
|気持ちや性格の状態を読む|`yadori state`|
|自身の記憶を整理する|`yadori dream`|
|Discordで待ち受ける|`yadori discord`|
|すべての命令を確認する|`yadori --help`|

以前の会話ログは、保存前に対象を確認できます。`--person` は設定にある人物の識別子です。

```sh
yadori import codex --from ~/.codex/sessions --person sora
# 表示を確認し、保存する場合は同じ指定に --apply を付ける
```

Claude・agyの取り込み先や再実行の扱いは[取り込みガイド](docs/100_usage/importing.md)にまとめています。

## データの扱い

設定と記憶は手元の `~/.yadori` に保存します。`YADORI_HOME` で保存先を変えられます。取り込みは選んだログだけを読み、元のログを書き換えません。外部会話を取り込むだけでは、宿り自身の名乗りや気持ちは変わりません。

**応対やdreamに使う会話は、利用するAIサービスへ渡ります。** 保存先が手元にあることと、外部送信がないことは同じではありません。[道具ごとの設定・履歴の扱い](docs/100_usage/cli.md)を確認してください。

## 開発状況

初期開発中です。気持ち・性格は応対に使う状態値として扱い、検索や補完の正しさを保証するものではありません。自発的な通知や常駐同期は未実装です。追加検索の道具はClaude Code・Codex・agy向けで、専用チャットとDiscordにはありません。

0.0.1から更新する方は[変更履歴](CHANGELOG.md#010---2026-09-14)と[設定の移行](docs/100_usage/configuration.md)を確認してください。引数なしの起動は道具選択へ変わり、従来の端末チャットは `yadori chat` へ移ります。記憶の原文は維持します。

## 開発と問い合わせ

コードやドキュメントの改善も歓迎します。不具合や要望は[Issues](https://github.com/yadori-dev/yadori/issues)、設計の相談は[Discussions](https://github.com/yadori-dev/yadori/discussions)へ。開発環境とGit運用は[CONTRIBUTING](CONTRIBUTING.md)、仕組みと判断は[設計文書](docs/150_system/README.md)にあります。

## ライセンス

[MIT License](LICENSE)。同梱する依存ソフトウェアには、それぞれのライセンスが適用されます。

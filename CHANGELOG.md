# 変更履歴

書き方は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、版の付け方は [Semantic Versioning](https://semver.org/lang/ja/) に従う。

1.0.0未満は初期開発版である。マイナー版の更新で、HTTP API、保存形式、出ていく先との約束が互換性なく変わることがある。互換性のない変更では、消えた入口、置き換える使い方、以前の保存先を読めるか、読めない場合にどう始めるかを書く。

**利用者の記憶の原文を失う移行はしない。** 新しい版が以前の保存先を読めない場合、そのファイルを変更・削除せず起動を断る。

## [Unreleased]

## [0.0.1] - 2026-09-11

最初期リリース。仕様や保存形式は今後変わることがある。

### Added

- 端末と Discord からの会話、記憶と状態の共有
- Claude Code と Codex を宿りとして起動する入口
- 会話の保存と思い出す処理、気持ちと性格の状態表示
- 記憶を読み直して整理する夢の手動実行
- 思い出す質の測定と、評価セットの下書き作成
- Ubuntu 24.04 / 26.04 と Debian 13 の amd64 向け apt 配布
- Git タグから製品版を求め、Release 発行から配布物と署名付き apt 配布元を自動で更新する仕組み
- 進め方、要求分析、後戻りが高い判断、用語集、層の構造

[Unreleased]: https://github.com/yadori-dev/yadori/compare/v0.0.1...develop
[0.0.1]: https://github.com/yadori-dev/yadori/releases/tag/v0.0.1

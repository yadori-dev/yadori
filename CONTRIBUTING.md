# Contributing to yadori

進め方の正典は [`docs/000_process.md`](docs/000_process.md) です。この文書はGit運用、開発環境、コードの書き方だけを定めます。

## ブランチ戦略（GitFlow）

| ブランチ | 役割 | 起点 | マージ先 |
|---|---|---|---|
| `main` | リリース済みの唯一の真実源。各コミットはタグ付きリリースに対応 | — | — |
| `develop` | 次期リリースの統合ブランチ | `main`（初回のみ） | `release/*` 経由で `main` へ |
| `feature/*` | 単一Issueの作業ブランチ | `develop` | `develop` |
| `release/*` | 版の確定とCHANGELOG | `develop` | `main`（タグ付与）+ `develop`（back-merge） |
| `hotfix/*` | リリース済み版への緊急修正 | `main` | `main`（タグ付与）+ `develop`（back-merge） |

feature ブランチは `feature/{issue番号}-{slug}` または `feature/{slug}`。release と hotfix は `release/0.1.0` のように版だけを付けます（`v` 接頭辞はタグ側）。

`main` と `develop` へ直接pushしません。`main` への PR は `release/*` と `hotfix/*` からだけです。

### 版と互換性

1.0.0未満は初期開発版です。HTTP API、保存形式、出ていく先との約束に互換性のない変更を含む場合はマイナー版を上げます（`0.1.x` → `0.2.0`）。パッチ更新は使い方を壊さない修正だけです。

0.xでは保存形式も互換性なく変えられます。ただし**利用者の記憶の原文を失う移行はしません**。新しい版が以前の保存先を読めない場合、そのファイルを変更・削除せず起動を断ります。

## コミット規約

[Conventional Commits](https://www.conventionalcommits.org/) に従います。PRタイトルもsquash merge時のコミットメッセージになるため同じ規約に従います。

使える type は `feat` `fix` `docs` `chore` `refactor` `test` `ci` `build` `perf` です。

Issueに対応するコミットは `git commit --trailer "Github-Issue:#<番号>"`、不具合の報告者がいる場合は `--trailer "Reported-by:<名前>"` を付けます。

一つのコミットは一つの論理的変更です。無関係な変更を混ぜません。

### AI生成フッターの禁止

コミットメッセージに、AIエージェントが自動挿入する生成元識別フッターを含めることを禁止します。コミット履歴の著者情報は Git の `author` / `committer` が表現するため、trailer による生成元識別は重複です。

`commit-msg` フック（`scripts/check-commit-msg.sh`）で遮断します。`just setup` でフックを導入してください。

## マージ戦略

`feature/*` → `develop` は squash merge、`release/*` と `hotfix/*` のマージは merge commit（no fast-forward）です。**rebase merge は使いません。**

`release` または `hotfix` を `main` へマージしたら、24時間以内に同じブランチを `develop` へ back-merge します。

## 認証情報が混ざったとき

pushする前に気づいた場合は、コミットを作り直してから push します。push済みの場合は、先に**その認証情報を無効化**してください。履歴の書き換えより先です。既に配られたものは、履歴から消しても取り消されません。

無効化したあと、`git filter-repo` で対象範囲のコミットを書き換え、`gitleaks git --no-banner --redact` で残りが無いことを確認し、feature ブランチだけを force-push します。

## 開発環境

必要なものは Python 3.12以上、[uv](https://docs.astral.sh/uv/)、[just](https://github.com/casey/just)、[lefthook](https://github.com/evilmartians/lefthook)、[gitleaks](https://github.com/gitleaks/gitleaks) です。

```console
$ just setup     # 依存の取得とGitフックの導入
$ just check     # 現在のコードと文書に対する全検査
$ just test      # テストだけ
```

動作確認には使い捨ての保存先を使います。**自分の宿りの記憶に対してテストを実行しません。** 環境変数 `YADORI_HOME` で保存先を切り替えます。

## 層の構造

`backend/src/yadori/` を四層に分け、依存を内向き一方向にします（[ADR-004](docs/010_decisions/ADR-004-層の依存を内向きにし内側を機能で割る.md)）。

```text
infrastructure → adapter → usecase → domain
```

各層の `__init__.py` に責任と依存の向きを書きます。層の内側は機能で分けます（`<layer>/<feature>/`）。`just check-deps` が向きを検査します。

## コーディング規約

- 業務の言葉で命名する。使う言葉は [`docs/150_system/用語集.md`](docs/150_system/用語集.md) が正典で、無い概念へ勝手に名前を付けない
- ロジックはそのデータと同じ場所に置く。データを取り出して外で処理しない
- 不正な状態を表現できなくする。不正な入力と状態は早期に失敗させる
- エラーハンドリングは境界（利用者の入力、外部API）にだけ置く。内部の例外は伝播させる
- 投機的な抽象化、将来用の汎化、使わない引数を書かない
- コメントは「なぜそうするか」だけ書く。「何をするか」はコードが語る
- 型を明示する。`Any` と型の言い換えは設計の敗北
- 重複は、間違った共通化で縛るよりましなことがある。共通化の前に「これは機能を縛らないか」を問う

### テスト

- テストは実装と同じコミットに含める
- 利用者の体験に近い順（受入・システム＞E2E＞結合＞単体）に設計する。上位ほど優先し、下位は数で網羅を補う
- 外から見える階層は、入口へ「何を渡すと何が返り、何が起きるか」だけで検証する。内部構造を覗かない
- モックはI/O境界（外部API、DB、時刻、模型の呼び出し）だけに留める。自分が書いたコードをモックしない
- 返却値を根拠なく仮定したモックを書かない。実際に観測した値を fixture として固定する
- **固定した観測値には、それが今も本物と一致するか確かめる契約テストを対で置く。** 外部の形が変わったとき、モックだけでは本番が壊れてもテストが緑のままになる。実物に当てるテストは遅いため通常の実行から分けてよいが、動かさないことにはしない

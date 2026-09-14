# ログの取り込みと起動前の準備

[READMEへ戻る](../../README.md) · [使い方の一覧](README.md)

## 起動前の準備

通常の `yadori`、`yadori claude`、`yadori codex`、`yadori agy`、`yadori chat` は、初回に起動前の準備を選べます。取り込み元と動作を人物ごとに保存し、次から外部会話の取り込み→自身の夢→会話の順に進みます。`yadori setting` の「起動前の取り込みと夢」で、自動・毎回選ぶ・実行しないを変更できます。

今回だけ変える場合は `yadori --prepare ask` で個別に選び、`yadori --prepare skip` で会話へ進みます。`--prepare auto` は保存済みの取り込み元を自動で処理します。質問できない入力では、設定済みの自動処理だけを行います。起動前に Ctrl+C を押すと実行中の子も止め、確定した分を残して会話へ進みます。

取り込みは新規200往復まで、夢も一回分までとし、残りを表示します。外部会話は取り込んだ時点で検索でき、宿り自身の夢や気持ちへ変換しません。取り込みが0件でも自身の未処理会話があれば夢を行います。夢では既存と同様に、選んだ会話を対話する道具へ渡します。保存先を確かめられない場合は起動を断ります。

## 手動で取り込む

宿りとして起こしていない三つのCLIの会話も、選んだ人物へ取り込めます。まず確認し、`--apply` を付けた場合だけ保存します。人物の識別子は `settings.toml` の `people` のキーです。

```console
yadori import claude --from ~/.claude/projects --person sora
yadori import claude --from ~/.claude/projects --person sora --apply
yadori import codex --from ~/.codex/sessions --person sora --apply
yadori import agy --from ~/.gemini/antigravity-cli/brain --person sora --apply
```

ファイルまたはディレクトリを指定でき、複数なら `--from` を繰り返します。agyは会話別の `.system_generated/logs/transcript_full.jsonl` を読みます。要約や入力履歴から応対を推測しません。現在の実物で確認した形式はClaude Code 2.1.270、Codex 0.154.0、agy 1.2.2です。古い形式などで完成を確認できない往復は理由を表示し、取り込みません。

完成した利用者発話と応対を原文で保存し、出典には元の道具・会話・発話を持ちます。検索時も外部の会話だと表示します。同じ出典の再実行やファイル移動で重複せず、原文が変われば上書きしません。事前確認の破損・競合では書き込まず、保存中の障害では確定した往復だけを残して件数を表示します。未完の末尾は次回へ残します。取り込み自体では名乗り・気持ち・思い出した回数を増やしません。

# ADR-020 Claude Code の会話履歴を宿り専用に分け、手元の設定は参照して使う

| 項目 | 内容 |
|---|---|
| 状態 | 有効 |
| 決定日 | 2026-09-07 |

## 判断

宿りとして起こす Claude Code は、一回の起動ごとに `YADORI_HOME/claude/sessions` の下へ専用の設定、会話履歴、未完の発話を置く。起動中はそのディレクトリの施錠を持ち続ける。終了後はディレクトリごと捨て、過去の Claude Code の会話は再開させない。宿りが持つ一往復の記憶だけを、道具をまたぐ正典とする（[ADR-016](ADR-016-応対は対話する道具を通して作り、その道具の文脈を持ち込まない.md)）。

普段の Claude Code から借りるものは、次に限定する。利用者設定、作業場所の共有設定と手元だけの設定から、起動のたびに現在値を読み、その回の専用設定へ写す。Claude Code には専用の利用者設定だけを読ませ、普段の利用者・作業場所・手元だけの設定を直接読ませない。

Claude Code 2.1.260 と同じ探索場所を保つ。共有設定は起動したディレクトリの `.claude/settings.json`、手元だけの設定と信頼の記録は Git の worktree が参照する元の作業場所から読む。ただし、そこに書かれた `/path` と相対の追加場所は、どちらも起動したディレクトリを基準に解決する。agents、commands、skills は起動したディレクトリから worktree 側のリポジトリ直下まで各階層を読み、直下から起動場所の順で重ねる。worktree 側の内容と元の作業場所にある信頼の記録を混同しない。

- 契約でログインした認証ファイルへの参照
- 利用者と作業場所の `agents`、`commands`、`skills` の中身
- 常時動くフック、監視、話し方の指示、利用者別の設定、永続データを必要としない、有効なプラグイン
- `permissions` の `allow`、`ask`、`deny`、`additionalDirectories`
- `enabledPlugins`、`extraKnownMarketplaces`、`model`、`effortLevel`、`modelSettings`、`theme`、`tui`、`statusLine`
- 全体の個人用 MCP と、起動した作業場所の `allowedTools`、`hasTrustDialogAccepted`、`enabledMcpjsonServers`、`disabledMcpjsonServers`、`disabledMcpServers`、`mcpServers`、`mcpContextUris`

作業場所に共有・手元だけの設定、agents、commands、skills、`.mcp.json` のいずれかがあるときは、普段の Claude Code でその作業場所を信頼済みの場合だけ借りる。起動場所からリポジトリ直下までにある agents、commands、skills は、コピーする全階層をこの確認へ含める。未信頼なら何も有効化して続けず、普段の `claude` で内容を読み信頼する手順を示して起動を断る。`.mcp.json` の接続先は、信頼に加えて普段の Claude Code で個別に有効化済みの名前だけを借りる。未判断の接続先があれば、普段の `/mcp` で判断する手順を示して起動を断り、無効化済みのものは借りない。これにより、専用の利用者設定へ写すことが普段の信頼確認を追い越さない。

設定の優先順は普段と同じく手元だけ、作業場所、利用者の順で解決してから写す。`permissions` の許可、確認、拒否の規則と追加の作業場所は、利用者、作業場所、手元だけの順に重複を除いて結ぶ。権限規則の `path` と `./path` は現在の作業場所、`~/path` はホーム、`//path` はファイルシステムの根を基準にするため値を変えない。設定元を基準にする `/path` だけは、利用者設定なら普段の設定ディレクトリ、作業場所と手元だけの設定なら起動した作業場所を基準に絶対パスへ解決し、`//path` の形へ直す。追加の作業場所の相対パスも同じ設定元の基準で絶対パスへ直す。これにより、専用の利用者設定へ移しても指す場所を変えない。

`enabledPlugins`、`extraKnownMarketplaces` の各項目は名前ごとに、`modelSettings` は AIモデルごとに、最も優先する設定を採る。それ以外の単独の値は、その値を持つ最も優先する設定を採る。借りたファイルと設定はその回の写しである。

`permissions.defaultMode`、`skipDangerousModePermissionPrompt` はどの設定からも借りず、`--dangerously-skip-permissions` と `--allow-dangerously-skip-permissions` も渡さない。作業場所では無効な危険モードの指定を専用の利用者設定へ写すと有効になってしまうためである。

MCP は設定へ平らに写さず、安全を確かめた利用者、作業場所、手元だけの接続先だけを一つの生成ファイルへ集め、`--mcp-config` と `--strict-mcp-config` で渡す。厳格にしないと、普段の `.mcp.json` が別に直接読まれ、除外した接続先が戻るためである。普段の `/mcp` で停止した名前は、利用者用と手元だけの接続先も含めて生成ファイルへ入れない。相対の命令と引数は設定ファイルの置き場ではなく Claude Code を起動した作業場所が基準になるため、値を変えずに写し、同じ作業場所から動かす。接続時に認証用の命令を動かす `headersHelper` は、生成ファイルへ移すと命令の作業場所と渡る認証環境が変わるため、その MCP を理由とともに警告して借りない。名前が重なる場合は、手元だけ、作業場所、利用者の順で最も優先するものを採る。定額契約から自動で来る Claude.ai の接続先は厳格な MCP 設定と `disableClaudeAiConnectors` で止める。

プラグイン自身の MCP は今回借りない。通常の MCP として生成すると、Claude Code が付ける接続先と道具の名前が変わり、プラグインの技能、下位の担当、既存の権限規則から指せなくなるためである。厳格な MCP 設定で直接読み込みも止め、構成ファイルに MCP があれば理由を警告する。別の場所または内容を直接指すフック、監視、話し方の項目があればプラグインごと借りず、構成ファイルを安全に読めないものも借りない。プラグインの技能、下位の担当、LSP（編集中のコードを調べる仕組み）は、他の除外条件に当たらなければ使える。

許したプラグインの導入済みキャッシュは、Claude Code の読み取り専用の種として `CLAUDE_CODE_PLUGIN_SEED_DIR` から参照する。利用者別の `pluginConfigs` があるもの、導入済みの本体が `${CLAUDE_PLUGIN_DATA}` または `${user_config.*}` を使うもの、普段の設定ディレクトリに永続データがあるものは、理由とともに警告して借りない。種は普段のキャッシュを更新せず、専用設定からの自動更新も行わない。

Claude Code の権限確認で利用者が「今後は確認しない」と選ぶと、Claude Code は作業場所の `.claude/settings.local.json` へ許可を書く。この利用者が選んだ変更は普段の Claude Code と共有し、次の宿りの起動でも借りる。テーマなど利用者設定へ書かれる変更は専用設定へ留まり、終了時に捨てる。yadori の起動準備は普段の設定を書き換えない。

利用者、作業場所、プラグインの常時動くフックと監視、`env`、`apiKeyHelper`、`outputStyle`、`pluginConfigs`、危険モードに関わる権限設定は借りない。普段のフックと宿りのフックが一つの完成条件に依存することと、別の指示が宿りの名乗りを変えることを避けるためである。管理者が強制する設定だけは除外できない。

宿りとして起こすときは `--setting-sources user` で専用の利用者設定だけを読ませ、命令行から最も強い利用者側の設定として次を渡す。管理者が強制する設定は上書きできない。

宿りのフックは、導入先に作られた `yadori` 命令を絶対パスで呼ぶ。起動した作業場所から Python の部品名を探させると、同名のファイルや部品を持つ作業場所のコードをフックとして誤って実行できるためである。

- 発話の直前に思い出し、返事の直後に覚え、失敗と終了時に未完の発話を捨てるフック
- `CLAUDE.md`、`CLAUDE.local.md`、rules（条件に応じて読む指示）の除外
- Claude Code 自身の自動記憶の停止と、他の設定によるフック全停止の解除
- 定額契約に結ばれた Claude.ai の接続先を自動で読む機能の停止
- 背景作業と会話中の予約の停止
- 宿りの名乗りと、気持ちの動きを返事の末尾へ書く頼み

定額契約であることを優先する。従量課金や別の接続先を優先させる環境変数は Claude Code へ渡さず、借り元の設定に同種の値があれば起動を断る。認証ファイルは専用ディレクトリから普段の認証ファイルへのシンボリックリンクとし、環境変数を除いた状態の `claude auth status --json` が `claude.ai` の定額契約と Anthropic 自身の接続を示す場合だけ起動する。

Claude Code が認証期限を更新するためリンク先へ直接書くことは、定額契約を継続するための変更として受け入れる。終了時に認証ファイルが同じリンクであることを確かめる。Claude Code がリンクを別ファイルへ置き換えた場合、その内容を普段の認証へ戻さず警告する。yadori 自身は認証情報の中身を読まず、写さず、書き換えない。

## 理由

普段の設定の置き場をそのまま使うと、宿りのフックと会話履歴が普段の Claude Code にも混ざる。設定全体を複製すると、別の会話で増えた道具と権限に追従できない。何を借り、何を専用にするかを項目ごとに固定し、起動のたびに読み直す。

Claude Code は `CLAUDE_CONFIG_DIR` で設定、会話履歴、プラグインの置き場を替えられる。`claudeMdExcludes` では、他の設定を保ったまま指示ファイルと rules だけを除外できる。命令行の `--settings` は、作業場所と利用者の設定より優先される。`--mcp-config` は起動した作業場所を基準に MCP を動かし、`--strict-mcp-config` はそれ以外の MCP を読ませない。これらの正式な入口で、宿りに必須の設定をその起動へ閉じる。

## 受け入れる不利益

利用者と作業場所のフック、名乗りを変える `outputStyle`、常時動くフック、監視、話し方の指示、利用者別の設定や永続データを持つプラグイン、プラグインが持つ MCP、`headersHelper` を持つ MCP、Claude.ai の接続先は、普段の Claude Code と同じにはならない。普段の設定に新しい種類の作業道具が増えても、自動では借りない。危険モードは普段の設定で選んでいても宿りの起動へ持ち込まない。権限確認で選んだ永続的な許可は普段の作業場所へも残る一方、テーマなど専用の利用者設定へ書かれた変更は次の起動へ残らない。

背景作業と会話中の予約は使えない。Claude Code のフックが渡す背景作業と予約の一覧はセッション全体のもので、どの発話から始まったかを正式な入力だけでは判定できない。非同期の返事を別の発話と結ぶより、対応を正しく固定できる機能に限る。

管理された指示は Claude Code の仕様上除外できない。組織の管理下で名乗りと衝突すると、同じ話し方を保証できない。管理された認証設定は定額契約の確認をすり抜ける可能性があるため、その形は今回の対象としない。

終了時に一時的な置き場を捨てられなかった場合は警告する。次の起動では、二十四時間を超え、かつ施錠を取れるディレクトリだけを捨てる。二十四時間を超えて動いている Claude Code の置き場は捨てない。それまでは、その回の会話履歴が宿りの置き場に残る。

## 見直す条件

Claude Code が、会話履歴の置き場だけを独立に指し、普段の認証と設定を読み取り専用で借りる正式な入口を持ったとき。借りたい設定の種類が増えたとき。管理された指示との衝突が利用者に起きたとき。

## 根拠

- [Claude Code の設定](https://code.claude.com/docs/en/settings)
- [Claude Code の権限](https://code.claude.com/docs/en/permissions)
- [Claude Code が作業場所の機能を探す範囲](https://code.claude.com/docs/en/agent-sdk/claude-code-features)
- [Claude Code の MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code の設定を調べる方法](https://code.claude.com/docs/en/debug-your-config)
- [Claude Code のプラグイン](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code のプラグインを読み取り専用で渡す方法](https://code.claude.com/docs/en/plugin-marketplaces#pre-populate-plugins-for-containers)
- [Claude Code が読む設定ディレクトリ](https://code.claude.com/docs/en/claude-directory)
- [Claude Code の指示と自動記憶](https://code.claude.com/docs/en/memory)
- [Claude Code のフック](https://code.claude.com/docs/en/hooks)
- [Claude Code の認証](https://code.claude.com/docs/en/authentication)

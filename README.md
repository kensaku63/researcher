# researcher

論文・YouTube・X調査のSkillを `.agents/skills/` に収録する。

- 論文: `paper-search`（外部 `paper-search-mcp` パッケージのCLIを直接利用）
- YouTube: `youtube-search`, `youtube-transcript`, `youtube-search-plan`, `youtube-search-report`, `youtube-search-insight`
- X: `x-search`, `x-bookmark-deep-research`, `x-search-plan`, `x-search-report`, `x-search-insight`

2026-09-28に以下のrepoの `.agents/skills/` と関連 `knowledge/` をファイル内容を変更せず取り込んだ。取得commitと全ファイルの一覧は [knowledge/search-imports.json](knowledge/search-imports.json) にある。

- https://github.com/kensaku63/paper-search
- https://github.com/kensaku63/youtube-research
- https://github.com/kensaku63/x-research-expert2

`.claude/skills/` の互換コピー、元agentのidentityと会話memoryは取り込まない。以前の独自最小版 `research-search` は削除済み。

依存パッケージと環境変数名は [environment.yaml](environment.yaml) に統合した。値はSessionの環境へ注入する。各APIの権限・クォータは別途必要。論文CLIの導入元は原本と同じGit URLで、導入時点の検証commitは `808e462a824ce6b26fdccbed352b4bf47d7b84cb`。

Skill内の相対パスのコマンドはagent repoをカレントディレクトリとして実行する。Python依存パッケージを導入した環境を有効にして使う。

```bash
cd "$AA_AGENT_DIR"
paper-search search "diffusion models" -s arxiv,semantic -n 5
python3 .agents/skills/youtube-search/scripts/search.py search --query "生成AI" --limit 5
python3 .agents/skills/x-search/scripts/search.py search --keywords "生成AI" --limit 5 --tool bird
```

取得失敗・認証未設定は検索成功と区別する。スクリプトが終了コード0を返してもJSON内の `limitations` と `next_human_actions` を確認する。認証情報の設定後に実APIで検証し、必要な改善を行う。

## 取り込み時の確認

41ファイルの原本とのバイト一致、Python 10ファイルの構文、実行入口4本の `--help`、論文CLIの21ソース一覧を確認。YouTube/X APIのキー未設定時の診断も確認した。外部サービス全機能の実検索は未検証。Python依存は検証用環境へ導入済み。字幕の音声処理用 `ffmpeg` は宣言のみで、このSessionには未導入。

2026-09-29に `x-search` をbird実検索で検証・改善した。bird はChromeのx.comログインCookieを自動で使えるため、`AUTH_TOKEN` / `CT0` 未設定でも動く。既定の `--sort top` は `min_faves` 段階検索で期間全体の注目投稿を集める（詳細は `x-search/SKILL.md`）。X API経路（`X_BEARER_TOKEN`）と `graph` は未検証。

Skill簡易validatorは9件合格。原本の `paper-search` のdescription内の山括弧、`x-search` の `disable-model-invocation` はvalidatorで不合格となるが、丸ごとコピーする指定に従って変更していない。`x-search` は必要時にSkillを明示して使用する。

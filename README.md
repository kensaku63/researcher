# agent-template

AAchat エージェント用テンプレートリポジトリ。

`aa agent create <team> <name>` で自動的にこのテンプレートからリポジトリが作成されます。

## 構造

- `CLAUDE.md` — エージェントの設定ファイル（`{{AGENT_NAME}}` が自動で置換される）
- `memory/` — チーム固有のメモリファイル
- `.claude/skills/` — エージェントのスキル定義

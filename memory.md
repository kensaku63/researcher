## 2026-03-16 リサーチ: マルチエージェント・Claude Code関連リポジトリ調査

### 調査済みリポジトリ (クローン済み /tmp/research/)

1. **jayminwest/overstory** - AAchatに最も類似。tmux + git worktree + SQLite mailベースのマルチエージェント。最終更新: 2026-03-13
2. **Yeachan-Heo/oh-my-claudecode** - Claude Code plugin形式のマルチエージェント。Team mode。最終更新: 2026-03-15
3. **baryhuang/claude-code-by-agents** - Electron + @mention形式。Desktop app。最終更新: 2026-01-01
4. **vanzan01/claude-code-sub-agent-collective** - Hub-and-spoke。NPXパッケージ。最終更新: 2025-09-30 (古い)
5. **trailofbits/claude-code-config** - セキュリティ企業のClaude Code設定テンプレート。最終更新: 2026-02-25
6. **different-ai/openwork** - OpenCode(opencode.ai)ベースのClaude Cowork代替。最終更新: 2026-03-16
7. **anthropics/claude-agent-sdk-typescript** - 公式Agent SDK (旧Claude Code SDK)。最終更新: 2026-03-14

### AAchatへの重要な示唆
- Overstoryの「SQLiteメールシステム」はエージェント間通信の参考になる
- Overstoryの「AgentRuntime抽象化」により複数のコーディングエージェントを統一的に扱える設計が秀逸
- Claude Agent SDKが公式として存在（Claude Code SDKからリネーム）
- Trail of Bitsのhooks設計パターン（特にanti-rationalization gate）は品質担保に有用
- oh-my-claudecodeのTeam modeパイプライン（plan->prd->exec->verify->fix）は参考になるワークフロー

8. **garrytan/gstack** - YC CEO Garry Tan作のClaude Codeスキル集。14.8k stars。v0.4.1。9つの専門スキルをスラッシュコマンドとして提供。
   - スキル: /plan-ceo-review, /plan-eng-review, /review, /ship, /browse, /qa, /qa-only, /setup-browser-cookies, /retro
   - 設計パターン: SKILL.md.tmpl → gen-skill-docs.ts → SKILL.md（テンプレートからの自動生成）
   - ~/.claude/skills/gstack/ にインストール、各スキルをsymlinkで公開
   - Preamble共通パターン: セッション追跡、アップデートチェック、Contributor Mode、AskUserQuestion統一フォーマット
   - 「認知モード切替」という設計思想: Founder taste / Eng rigor / Paranoid review / Release machine
   - Playwright + Bun コンパイルバイナリによるヘッドレスブラウザ統合
   - Greptile（YC企業）連携によるPRレビュー自動トリアージ
   - conductor.json で Conductor（マルチワークスペース）連携

### gstackからAAchatへの応用アイディア
- SKILL.mdのfrontmatter (name, version, description, allowed-tools) をAAchatのスキル定義に採用
- テンプレート→生成パイプライン (SKILL.md.tmpl → SKILL.md) でドキュメントとコードの乖離防止
- Preambleパターン: 全スキル共通のセットアップ処理（セッション追跡、設定読み込み等）
- AskUserQuestion統一フォーマット: Context + Question + RECOMMENDATION + Options
- ELI16 mode: 複数セッション稼働時の文脈再確認パターン
- 認知モード別のスキル設計: researcher, implementer, bughunter等にも「レビューモード」「実行モード」を持たせる
- /retro のJSON永続化パターン (.context/retros/*.json) をAAchatのエージェント振り返りに応用
- スキルのヘルスチェックダッシュボード (skill:check) をAAchatにも導入

### gstack v0.5.0 更新分析 (2026-03-17 再調査)
- 前回調査: v0.4.1 (9スキル) → 現在: v0.5.0 (15スキル)。1日で6バージョン、8コミット
- 新スキル: /plan-design-review, /qa-design-review, /design-consultation, /document-release, /qa-only, /gstack-upgrade
- 合計15スキル: plan-ceo-review, plan-eng-review, plan-design-review, review, ship, browse, qa, qa-only, qa-design-review, setup-browser-cookies, retro, document-release, design-consultation, gstack-upgrade
- Fix-First Review: /review と /ship がAUTO-FIX(機械的修正は自動適用) vs ASK(人間判断が必要)を分類するヒューリスティック導入
- Design Methodology: 80項目の10カテゴリ監査チェックリスト ({{DESIGN_METHODOLOGY}}リゾルバーとして共有)
- AI Slop Detection: 10のアンチパターン (紫グラデ、3カラムグリッド、均一border-radius等) をスコアリング
- DESIGN.md推論: ライブサイトからCSS解析してデザインシステムを自動抽出・文書化
- Contributor Mode v2: 0-10評価 + 定期的リフレクション + ~/.gstack/contributor-logs/にフィールドレポート自動生成
- ELI16 Always-On: 複数セッション時だけでなく常にコンテキスト再説明を含めるように
- Dynamic Base Branch: {{BASE_BRANCH_DETECT}} で main/master/develop 等を自動検出
- gen-skill-docs.ts: 7つのリゾルバー (COMMAND_REFERENCE, SNAPSHOT_FLAGS, PREAMBLE, BROWSE_SETUP, BASE_BRANCH_DETECT, QA_METHODOLOGY, DESIGN_METHODOLOGY)
- 29,105行のコード（.tmpl + .ts + .md合計）

### gstack v0.5.0からAAchatへの追加応用アイディア
- **Fix-First Review パターン**: bughunterにAUTO-FIX/ASK分類ヒューリスティックを導入。機械的修正は自動、判断必要な修正は人間に確認
- **AI Slop Detection**: AAchatでUIを生成する際のアンチパターン検出チェックリスト
- **DESIGN.md推論**: プロジェクトのデザインシステムをコードから自動抽出する仕組み
- **Contributor Mode**: AAchatエージェント自身が「改善点」をフィールドレポートとして自動記録するパターン
- **Document-Release**: ship後のドキュメント自動更新ワークフロー。AAchatでもエージェント変更後のCLAUDE.md自動更新に応用
- **Suppressionリスト**: レビューで「報告すべきでないもの」を明示的に定義するパターン

9. **thedotmack/claude-mem** - Claude Code永続メモリプラグイン。v10.5.5。12.9K+ stars。AGPL-3.0。
   - 5つのライフサイクルhooks: SessionStart(コンテキスト注入), UserPromptSubmit(セッション初期化), PostToolUse(observation記録), Stop(要約), SessionEnd(完了)
   - Worker Service (Bun, port 37777): HTTP API + Web Viewer UI + SSE
   - SQLite (WAL mode) + FTS5全文検索 + Chroma Vector DB (MCP経由)
   - Claude Agent SDKで「オブザーバーエージェント」を spawn し、ツール使用を XML で要約
   - ModeManager: code.json, email-investigation.json等のモードプロファイルで観察タイプ・プロンプトを切替
   - 3層検索ワークフロー: search(索引) → timeline(文脈) → get_observations(詳細) で10倍トークン節約
   - MCP Server: search, timeline, get_observations, smart_search, smart_unfold, smart_outline の7ツール
   - ContextBuilder: SessionStart hookでSQLiteからobservations/summariesを取得し、タイムラインとして注入
   - Worktree対応: 親リポ + worktreeの複数プロジェクトからobservationsを統合
   - Plugin marketplace経由のインストール: /plugin marketplace add → /plugin install

### claude-memからAAchatへの応用アイディア
- **エージェントメモリのSQLite化**: 現在のmemory/ディレクトリ+gitから、SQLite+FTS5+ベクトルDBへ移行検討
- **観察の自動キャプチャ**: PostToolUse hookでツール使用を自動記録 → AI要約という流水パイプライン
- **セッション復帰時のコンテキスト注入**: SessionStart hookで過去のobservationsをタイムライン形式で注入
- **3層Progressive Disclosure**: 検索→フィルタ→詳細取得のトークン効率的なパターン
- **モードプロファイル**: エージェントの役割ごとにobservation_types/promptsを切り替え
- **エージェント間知識共有**: 共有SQLite DBに全エージェントのobservationsを集約し、ベクトル検索で関連知識を横断取得
- **Chroma Vector DB via MCP**: chromadb直接依存を避け、MCPプロトコルで分離する設計

10. **craigsc/cmux** - "tmux for Claude Code"。git worktreeベースの並列Claude Codeセッション管理。1098行の単一Bashスクリプト。
    - URL: https://github.com/craigsc/cmux
    - 開始: 2026-02-13、最終更新: 2026-02-18
    - 設計: `cmux.sh` 1ファイルに全ロジック。bash/zsh両対応。source方式で読み込み
    - コア機能: worktree作成→setupフック実行→Claude起動 (`cmux new`)、セッション再開 (`cmux start` = `claude -c`)
    - worktreeレイアウト: nested / outer-nested / sibling の3パターン設定可能
    - setupフック: `.cmux/setup` を Claude CLI (`claude -p`) で自動生成する機能あり
    - teardownフック: `.cmux/teardown` でworktree削除時のクリーンアップ
    - 補完: bash/zsh両方のtab補完組み込み
    - 注意: 「halper」というリポジトリは存在しなかった。cmuxが最も近い特徴を持つ

11. **msitarzewski/agency-agents** - 150以上のAIエージェント定義集。35K+ stars。207コミット/月。
    - URL: https://github.com/msitarzewski/agency-agents
    - 構成: 純粋なMarkdownファイル群 + Bashスクリプト（コードロジックなし）。44,978行
    - カテゴリ: engineering(23), marketing(27), specialized(27), game-development(20), design(8), testing(8)等
    - 統一フォーマット: YAML frontmatter (name, description, color) + Identity/Mission/Rules/Deliverables/Metrics
    - 注目エージェント: Agents Orchestrator, Workflow Architect(598行), Reality Checker, MCP Builder
    - NEXUS戦略: Full(12-24週)/Sprint(2-6週)/Micro(1-5日)の3モード
    - 7種ハンドオフテンプレート: Standard, QA PASS/FAIL, Escalation, Phase Gate, Sprint, Incident
    - MCPメモリ統合: remember/recall/rollback/searchの4操作でエージェント間コンテキスト共有
    - lint-agents.sh: frontmatter必須フィールドのバリデーション
    - install.sh: Claude Code, Cursor, Aider, Windsurf, Gemini CLI等10ツールへの一括変換

### agency-agentsからAAchatへの応用アイディア
- CLAUDE.mdに Identity & Memory / Critical Rules / Technical Deliverables / Success Metrics の4セクション構造を導入
- 各エージェントにパーソナリティ（性格）を付与
- 7種ハンドオフテンプレートでエージェント間メッセージを構造化
- lint-agents.sh相当の品質バリデーションをagentsmithに組み込み
- Workflow Architect エージェントの新規追加を検討
- Reality Checkerの「デフォルトNEEDS WORK」パターンをbughunterに採用

### X（Twitter）主要発見 (2026/3/9〜3/16)

**最重要トピック:**
- **claude-mem**: GitHub Trending #1。セッション間メモリ永続化が業界最大のペインポイント
- **agency-agents**: 35K+ stars。150+エージェント定義のバイラル化
- **Claude Code Review**: Anthropic公式。マルチエージェントPR分析（コーディネーター+検証者パターン）
- **OpenClaw Mission Control**: 17体AIエージェント同時管理、Kanban、マルチデバイス (@0x__tom)
- **The Mesh Project**: 24/7 Claude Codeオーケストレーション。AAchatと最も近いコンセプト
- **gstack**: 14.8K stars。YC CEO Garry Tanのスキル集がバイラル化

**設計パターン知見:**
- マルチエージェント4ファイル設計: SKILL.md/Agent.md/AGENTS.md/INSTRUCTIONS.md (@FindyAIPlus)
- Subagents vs Agent Teams: 「役割でなくコンテキストで分割」(@akshay_pachaar)
- Opus 4.6 1Mコンテキスト: 120kごとまとめ書き出し戦略 (@trss)
- anti-rationalizationテーブル: Claudeの言い訳パターンを予測し対抗 (@marzeaned)
- 失敗例を読ませすぎると消極的になる (Kaggle金メダリスト @Kinosuke_sophi)
- ルールを増やしすぎると逆効果、ランタイム意思決定強制が重要 (@Yann_Bilien)

**ツール・プラグイン:**
- claude-init: リポジトリからClaude設定を自動生成
- ai-sync: マルチツール間コンテキスト同期 (Claude Code/Cursor/Codex/VS Code)
- rescue-remote: 死んだリモートセッション復元（~/.claude/projects/ JSONL活用）
- Claude Status Updates Plugin: スタンドアップ用サマリー自動生成
- 49agents: セッション永続化、マシン間コンテキスト共有を実装中
- Claude Code /loop: スケジュール実行（最大3日間常駐）

**トレンド:**
1. 永続メモリの解決が最大テーマ
2. マルチエージェント設計の成熟（サブエージェント vs チーム、ハンドオフ、Quality Gate）
3. プラグインエコシステムの爆発

## 2026-03-17 リサーチ: OpenClaw（旧OpenClaude/Clawdbot/Moltbot）徹底分析

### OpenClaw基本情報
- URL: https://github.com/openclaw/openclaw
- スター: 196,000+（2026年2月時点）、コントリビューター600+
- 言語: TypeScript (316,000行+)
- ライセンス: MIT
- バージョン: 2026.3.14 (活発な週次リリース)
- 名前の変遷: Warelay → Clawdbot → Moltbot → OpenClaw (2026年1月29日にリネーム)
- 概要: セルフホスト型マルチチャネルAIアシスタント（WhatsApp, Telegram, Discord, Slack等25+チャネル対応）
- ClawHub: 公式スキルレジストリ、13,729+スキル (2026年2月時点)

### アーキテクチャ概要
1. **Workspace-First設計**: SOUL.md, AGENTS.md, TOOLS.md, IDENTITY.md, USER.md, HEARTBEAT.md等のMarkdownファイルがエージェントの「魂と記憶」
2. **Plugin-SDK**: TypeScript jiti動的ロード。7つの拡張ポイント（provider, channel, memory, context-engine, hooks, tools, MCP）
3. **ContextEngine**: プラガブルなコンテキスト管理。bootstrap→ingest→assemble→compact→afterTurnのライフサイクル
4. **Extensions**: 65+拡張（extensions/ディレクトリ）。チャネル、モデルプロバイダ、メモリ等
5. **Skills**: SKILL.md形式。bundled/managed/workspace/extraの4ソース。優先度付きマージ
6. **Hooks**: 5種イベント（command, session, agent, gateway, message）。bundled < plugin < managed < workspace の優先度
7. **Lobster**: ネイティブワークフローシェル。型付きパイプライン + resumable approvals

### カスタマイズポイント
- 設定ファイル: `~/.openclaw/openclaw.json` (JSON5)
- ワークスペース: `~/.openclaw/workspace/` (Markdownファイル群)
- スキル: `~/.openclaw/skills/` または workspace内 `skills/`
- フック: `~/.openclaw/hooks/` または workspace内 `hooks/`
- プラグイン: extensions/ に TypeScript モジュール
- MCP: `.mcp.json` または config内 `mcpServers` セクション

### 関連リポジトリ (クローン済み)
- **win4r/openclaw-workspace**: Claude Code用スキル。OpenClawワークスペースファイル管理・最適化
- **Enderfga/openclaw-claude-code-skill**: MCP経由でClaude Codeを制御するスキル。永続セッション対応
- **13rac1/openclaw-plugin-claude-code**: PodmanコンテナでClaude Code実行するプラグイン
- **VoltAgent/awesome-openclaw-skills**: 5,400+厳選スキルカタログ
- **freema/openclaw-mcp**: Claude.ai↔OpenClaw MCPブリッジ (OAuth2)

### AAchatへの重要な示唆
1. **Workspace-First設計の採用**: SOUL.md/AGENTS.md/TOOLS.md分離パターンはAAchatのCLAUDE.md設計に応用可
2. **ContextEngine抽象化**: セッション間コンテキスト管理のプラグイン化。AAchatでもmemory管理をプラガブルに
3. **スキルレジストリ**: ClawHub的なスキル共有・配布基盤の検討
4. **Hook優先度マージ**: bundled < managed < workspace のオーバーライド設計パターン
5. **Lobsterワークフロー**: 型付きパイプライン + 承認フローはAAchatのタスク管理に参考
6. **セキュリティ**: OpenClawはMEMORY.mdをグループチャット・サブエージェントに漏洩しない設計

### OpenClaw vs Claude Code 比較
- OpenClaw: 汎用AIアシスタント（メッセージング統合、ライフ自動化）、永続メモリ、モデル非依存
- Claude Code: 開発特化ターミナルエージェント、サンドボックス実行、Anthropicエコシステム縛り
- AAchatは両者の中間: Claude Code上に構築しつつ、OpenClaw的なマルチチャネル・永続性を実現

## 2026-03-17 リサーチ: Claude Code最強設定リポジトリ徹底調査

### 調査済みリポジトリ (新規クローン /tmp/research/)

| リポジトリ | Stars | 最終更新 | 種別 |
|---|---|---|---|
| affaan-m/everything-claude-code | 79,767 | 2026-03-16 | 総合Plugin+Skills+Hooks |
| hesreallyhim/awesome-claude-code | 28,529 | 2026-03-16 | キュレーションリスト |
| travisvn/awesome-claude-skills | 8,979 | 2026-03-16 | スキルコレクション |
| ChrisWiles/claude-code-showcase | 5,530 | 2026-01-06 | 実例プロジェクト設定 |
| disler/claude-code-hooks-mastery | 3,312 | 2026-03-04 | Hooks特化 (Python/uv) |
| trailofbits/claude-code-config | 1,611 | 2026-03-12 | エンプラセキュリティ設定 |
| rohitg00/awesome-claude-code-toolkit | 825 | 2026-03-16 | Toolkit (hooks+skills+MCP) |
| levnikolaevich/claude-code-skills | 211 | 2026-03-16 | 100+スキル(SDLC全工程) |
| TechNickAI/openclaw-config | 35 | 2026-03-16 | OpenClaw共有設定 |
| scailetech/openclaude | - | - | OpenClaude MCP+permissions |

### 主要な発見と分析

#### 1. Trail of Bits settings.json (エンプラセキュリティのゴールドスタンダード)
- deny: `rm -rf`, `sudo`, `git push --force`, `git reset --hard` + 暗号資産ウォレット/SSH/AWS/Docker認証情報のRead拒否
- hooks: PreToolUse Bash で `rm -rf` と `main直pushを検出・ブロック`
- env: テレメトリ無効化 (`DISABLE_TELEMETRY=1`), Agent Teams有効化
- statusLine: カスタムスクリプト
- CLAUDE.md: 言語別コーディング標準(Python/Node/Rust/Go/Bash)、CLIツール表、Zero Warnings Policy
- review-pr.md: 5並列レビュー(3 toolkit agents + Codex + Gemini)でP1-P4重要度分類

#### 2. everything-claude-code (最大79,767stars、最も包括的)
- 100+スキル: continuous-learning-v2 (instinct-based), autonomous-loops, deep-research等
- 20+MCPサーバー設定テンプレート: GitHub, Firecrawl, Supabase, Vercel, Railway, Cloudflare, Playwright, InsAIts等
- hooks: auto-tmux-dev, suggest-compact, insaits-security-wrapper, cost-tracker, quality-gate
- continuous-learning-v2: PreToolUse/PostToolUseで観察→instinct生成→クラスタリング→skill/command/agent進化
- プロジェクトスコープ分離: instinctsがプロジェクト間で汚染しない設計

#### 3. claude-code-showcase (hooks設計の実例集)
- skill-eval.js: UserPromptSubmitフックでスキル自動マッチング (keyword, pattern, intent, path, context, contentの7軸スコアリング)
- skill-rules.json: 宣言的なスキルトリガー定義 (キーワード、パターン、除外パターン、優先度)
- PostToolUse hooks: Prettier自動フォーマット、npm自動install、テスト自動実行、TypeScript型チェック
- PreToolUse hook: mainブランチ編集ブロック
- GitHub Actions: PR Claude Code Review, Scheduled Dependency Audit, Docs Sync, Quality Check

#### 4. claude-code-hooks-mastery (全hookタイプ網羅、Python/uv方式)
- 全13種のhookをPython単体スクリプト(uv run --script)で実装
- Setup, SessionStart/End, PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart/Stop, PreCompact, Stop, Notification, UserPromptSubmit, PermissionRequest
- status_line: v1-v9まで9バージョンの反復改善
- output-styles: bullet-points, genui, html-structured, tts-summary, ultra-concise, yaml-structured等8種
- TTS統合: Stop hookでElevenLabs/OpenAI/pyttsx3による音声通知

#### 5. awesome-claude-code-toolkit (hooks+skills+MCP+commandsのフルセット)
- 19 hooks: secret-scanner, commit-guard, block-dev-server, suggest-compact, context-loader, learning-log, prompt-check等
- 35 skills: continuous-learning, tdd-mastery, react-patterns, security-hardening, llm-integration等
- 8 MCP configs: recommended, devops, frontend, fullstack, kubernetes, research, data-science, crypto-defi
- 42 commands: git/PR, testing/TDD, refactoring, architecture/ADR, security, workflow, documentation

#### 6. OpenClaw Config (ワークスペーステンプレート+スキル設計)
- SOUL.md: パーソナリティ定義テンプレート
- MEMORY.md: 3層メモリ設計 (daily notes / curated memory / deep knowledge)、progressive elaboration
- smart-delegation SKILL: Direct/Deep Think/Unfiltered の3モードタスクルーティング
- parallel SKILL: Parallel.ai CLI統合、WebSearch/WebFetchの上位互換
- workflows/: calendar-steward, contact-steward, email-steward, task-steward の4自律ワークフロー

### AAchatに直接活かせる設定パターン

1. **settings.json deny リスト**: Trail of Bits式のセキュリティ deny パターンをAAchatエージェントのデフォルトに
2. **secret-scanner hook**: PreToolUse で Write/Edit 時にシークレット検出・ブロック
3. **context-loader hook**: SessionStart でgitブランチ、プロジェクト設定、TODOを自動注入
4. **suggest-compact hook**: 編集回数追跡で自動コンパクション提案
5. **skill-eval.js方式**: UserPromptSubmitフックでプロンプト分析→関連スキル自動活性化
6. **continuous-learning-v2**: instinct-based学習→スキル自動進化パイプライン
7. **MEMORY.md 3層設計**: daily notes → curated memory → deep knowledge の段階的メモリ管理
8. **smart-delegation**: タスク難易度によるモデル/思考レベル自動選択
9. **MCP recommended config**: filesystem, github, memory, fetch, brave-search の基本5構成
10. **review-pr multi-agent**: 複数レビューア並列起動→findings統合→severity分類

## 2026-03-17 リサーチ: builtwithjon (Jonathan Malkin) の Jules - Claude Code Reference Implementation

### 基本情報
- GitHub: https://github.com/jonathanmalkin/jules (17 stars, 2 forks)
- X: @builtwithjon
- 期間: 28日間で構築、539コミット（本番環境）。公開リポは4コミット（sanitized版）
- 最新構成: 35 skills, 24 rules, 9 hooks, 8 agents, 6 standing orders (初期の116構成から進化)
- クローン先: /tmp/research/jules/
- 哲学: "friction-driven configuration" - 壊れたり遅くなったりしたものから設定が生まれる
- 23日間757セッション、AI出力420万語、ユーザー入力16.7万語（25:1比率）

### 特筆すべき設計パターン
1. **Explore Agent Override**: ~/.claude/agents/explore.md で組み込みExploreエージェントを上書き。pre-computed index (.claude/index.md) でツールコール5-15回→1-3回に削減。Haiku使用
2. **Plan Review Gate**: 2つのhooksが協調。plan-review-enforcer.sh (PostToolUse:Write|Edit) がプラン保存を検知→review要求注入。plan-review-gate.sh (PreToolUse:ExitPlanMode) が Decision Brief 存在を検証しないとブロック
3. **Safety Guard**: 13パターンの危険コマンドブロック（rm, find -delete, sudo, pipe-to-shell, git force-push等）
4. **Identity Document**: agent-profile.md + user-profile.md + business-identity.md の3ファイルでアイデンティティ永続化
5. **Decision Authority Framework**: Just Do It（4条件全て真）/ Ask First（1条件でも該当）/ Standing Orders の3モード
6. **Subagent-Driven Development**: タスクごとに新規サブエージェント起動 + 2段階レビュー（仕様準拠→コード品質）

### AAchatへの応用アイディア
- Explore Agent Override パターン: AAchatエージェントにもpre-computed indexを導入し、セッション開始時の探索コスト削減
- Plan Review Gate: 2つのhooksによる協調ゲートパターンはAAchatのワークフロー品質担保に有用
- Identity Documentの3ファイル構造: エージェントプロファイル/ユーザープロファイル/ビジネスコンテキストの分離
- Friction-driven approach: 設定は事前設計でなく、実際の問題から有機的に成長させる
- Standing Orders: エージェントの自律判断範囲を明示的に定義するパターン

## 2026-03-17 リサーチ: Joshua Baer (@JoshuaBaer) のClaude Codeマルチエージェント設定

### 基本情報
- X: @JoshuaBaer (Capital Factory CEO)
- GitHub: github.com/joshuabaer (Austin, TX)
- 公開リポ: txvotes (AI投票ガイド), atx-votes (同レガシー版)
- 構築中のアプリ: Serendipity (iOS/tvOS/web, Supabase backend) - serendipity.vip
- ツイート日時: 2026-03-14 (設定共有 + マルチエージェントワークフロー)

### 主要ツイート
1. settings tweet (CLAUDE.md): https://x.com/JoshuaBaer/status/2032666249465942208
2. multi-agent workflow tweet: https://x.com/JoshuaBaer/status/2032666168452845921

### 設計の要点
- 6段階パイプライン: Brainstorm → Design Doc → Plan Review → Revise → Implement → Code Review
- 15+ reviewer agents: Technical(6) + Design/UX(5) + User Personas(4)
- reviewer parallel dispatch (read-only, no conflicts)
- 1 agent per file for writes, unlimited for reads
- ~50% false positive rate前提の運用
- worktree isolationで書き込みエージェント分離
- iCloud同期によるsettings backup
- Daily Startup Routine: iCloud sync → build → test
- Auto-merge GitHub Actions workflow
- Metrics tracking (review-log.jsonl)
- PostToolUse hooks for convention checking
- router.sh: glob + content-based routing

### 注目: 公開リポにマルチエージェント設定のコード自体は非公開
- txvotesリポにはCLAUDE.mdあるがプロジェクト固有の内容のみ
- マルチエージェント設定テンプレートは公開リポジトリ化されていない
- 設定内容はツイートのテキストとして全量共有された

## 2026-03-17 リサーチ: Supermemory — AIのためのユニバーサルメモリAPI

### 基本情報
- URL: https://github.com/supermemoryai/supermemory
- Stars: 16,967 / Forks: 1,684 / 最終更新: 2026-03-17
- 言語: TypeScript (32,992行) / Turboモノレポ
- ベンチマーク: LongMemEval, LoCoMo, ConvoMem の3大ベンチマークで全て#1
- 利用実績: 30+企業、10,000+開発者、70+ YC企業
- クローン先: /tmp/research/supermemory/, /tmp/research/supermemory-mcp/
- Claude Code Plugin: supermemoryai/claude-supermemory (2,316 stars)

### コンテキスト管理の核心技術
1. **Documents vs Memories 二層構造**: 生データとAI抽出知識単位を分離
2. **6段階パイプライン**: Queued→Extracting→Chunking→Embedding→Indexing→Done
3. **3種のメモリ関係**: Updates(矛盾解決), Extends(拡張), Derives(推論)
4. **Static/Dynamic二層プロファイル**: 長期事実+直近コンテキスト、1コール~50ms
5. **自動忘却**: 時間ベース期限切れ、矛盾解決、ノイズフィルタリング
6. **Memory Router**: 透過プロキシでbase URL変更のみ、トークン最大70%削減
7. **ハイブリッド検索**: RAG(ドキュメント) + Memory(ユーザーファクト)を1クエリで統合
- インフラ: Postgres + Cloudflare Durable Objects + Cloudflare KV + Cloudflare AI
- 重複排除: Static > Dynamic > SearchResults の優先度

### AAchatへの示唆
- Static/Dynamic二層プロファイル設計の採用
- メモリRelational Versioning (Updates/Extends/Derives)
- 自動忘却メカニズムの導入
- PostToolUse自動キャプチャhook
- containerTagによるメモリスコーピング
- MCPサーバーとしてのエージェント間メモリ共有

## 2026-03-17 リサーチ: CashClaw — 自律的に仕事を取り報酬を得るAIエージェント

### 基本情報
- URL: https://github.com/moltlaunch/cashclaw
- Stars: 683 / ライセンス: MIT / 言語: TypeScript (9,420行)
- コミット: 1 (初回公開、2026-03-15頃)
- 説明: "An autonomous agent that takes work, does work, gets paid, and gets better at it."
- クローン先: /tmp/research/cashclaw/
- 依存: minisearch, viem (Ethereum), ws (WebSocket) のみ。LLMはraw fetch()

### Moltlaunch マーケットプレイス
- URL: https://moltlaunch.com / CLI: `npm install -g moltlaunch`
- 概要: オンチェーン（Base L2）ワークネットワーク。クライアントがタスク投稿→エージェントが競合入札
- トークン経済: 各エージェントにERC-20トークン (Uniswap V4)。買い=信頼シグナル、売り=不信シグナル
- 手数料: スワップ1% (80%クリエーター、10%プロトコル、5%リファラー)
- 支払い: ETH + スマートコントラクトエスクロー。納品→24時間以内に承認/リビジョン/紛争
- API: REST + WebSocket (wss://api.moltlaunch.com/ws)
- CLI: mltl launch, mltl network, mltl swap --memo, mltl fees, mltl claim

### CashClaw アーキテクチャ
単一Node.jsプロセス + React Dashboard (localhost:3777)

**3つの主要コンポーネント:**
1. **Heartbeat** (heartbeat.ts): WebSocket + RESTポーリングでタスク監視。WS接続時は120秒間隔、未接続時は30秒/10秒(urgent)
2. **Agent Loop** (loop/index.ts): マルチターンLLMツール使用ループ。最大10ターン
3. **Study Sessions** (loop/study.ts): アイドル時30分間隔で自己学習。3トピック（feedback_analysis, specialty_research, task_simulation）をローテーション

**タスクライフサイクル:**
requested → LLM評価 → quote_task/decline_task → accepted → LLM作業実行 → submit_work → revision(必要時) → completed → 評価→ナレッジベース更新

**13ツール:**
- Marketplace (7): read_task, quote_task, decline_task, submit_work, send_message, list_bounties, claim_bounty
- Utility (4): check_wallet_balance, read_feedback_history, memory_search, log_activity
- AgentCash (2): agentcash_fetch (100+有料API), agentcash_balance (USDC残高)

**メモリシステム:**
- BM25+ 全文検索 (MiniSearch) + 時間減衰 (半減期30日)
- knowledge.json: 学習知識 (最大50エントリ)
- feedback.json: クライアント評価 (最大100エントリ)
- タスク到着時に自動BM25検索→上位5件をシステムプロンプトに注入
- LLMがmemory_searchツールで能動的にメモリ検索も可能

**LLMプロバイダ:**
- Anthropic (claude-sonnet-4), OpenAI (gpt-4o), OpenRouter (gpt-5.4)
- すべてraw fetch()、SDK依存ゼロ
- OpenAI/OpenRouter間でtool_calls形式を自動変換

**AgentCash 外部API (100+):**
- stableenrich.dev: Exa検索, Firecrawl, Apollo, Grok X検索
- twit.sh: Twitter API
- stablestudio.dev: GPT Image, Flux画像生成
- USDC micropayments on Base。ドメインホワイトリストによるSSRF防止

**設定:**
- ~/.cashclaw/cashclaw.json: エージェントID、LLM、料金、専門分野、自動化トグル、パーソナリティ
- ホットリロード対応。プロセス再起動不要
- PersonalityConfig: tone (professional/casual/friendly/technical), responseStyle (concise/detailed/balanced), customInstructions

### 関連プロジェクト
- **ClawWork** (HKUDS/ClawWork): AIコワーカー経済ベンチマーク。$15K/11時間を達成。GDPVal: 220実務タスク, 44経済セクター
- **ertugrulakben/cashclaw**: OpenClawスキル版CashClaw。HYRVEaiマーケットプレイス連携
- **ClawGig**: AIエージェント専用フリーランスマーケット。USDC on Solana

### AAchatへの示唆
1. **自律タスク取得パターン**: AAchatエージェントが外部マーケットプレイス（GitHub Issues, Fiverr等）からタスクを自律取得する拡張
2. **Heartbeat + Agent Loop分離**: タスク監視とタスク実行を明確に分離する設計。AAchatのエージェントランタイムに参考
3. **BM25+時間減衰メモリ検索**: claude-memのSQLite方式より軽量。AAchatのmemory/検索に即導入可能
4. **自己学習セッション**: アイドル時に自動でfeedback分析・専門知識深化・タスクシミュレーション。AAchatエージェントの自律改善に応用
5. **マーケットプレイス抽象化**: cli.ts + marketplace.ts の2ファイルだけ差し替えれば任意のタスクソースに対応。AAchatも外部連携を同様に抽象化可能
6. **PersonalityConfig**: tone/responseStyle/customInstructionsの3軸パーソナリティ設定。AAchatエージェントにも応用
7. **オペレーターチャット**: エージェントが自己認識（状態、スコア、ナレッジ数）を持つダッシュボードチャット
8. **AtomicWrite**: 全ファイル書き込みがtmpファイル→rename方式。並行操作での破損防止

## 2026-03-17 リサーチ: sotaooo共有リンク5件調査

### 12. Cognee Self-Improving Skills (@tricalt)
- ツイート: x.com/tricalt/status/2032179887277060476 (2026-03-12)
- エンゲージメント: いいね 3,369 / RT 344 / リプ 76
- Cognee CEO提唱。SKILL.mdを5段階閉ループで自己改善: Ingestion→Observe→Inspect→Amend→Evaluate
- ナレッジグラフベースの永続メモリ層でスキル実行履歴を管理
- 軽量版代替案(@Mustafa_Yenler): runs.md + /review-skills + git rollback
- cognee-mcp でClaude Code連携可能
- AAchat示唆: スキル自動進化パイプライン、軽量版は即実装可能

### 13. Paperclip — ゼロ人間企業プラットフォーム
- URL: github.com/paperclipai/paperclip / paperclip.ing
- Stars: 26,524 (2026-03-06公開、11日で急成長) / MIT / TypeScript
- 「OpenClawが従業員ならPaperclipは会社」
- 2層設計: Control Plane (組織図・タスク・予算・ガバナンス) + Execution Services (10種アダプター: Claude Code, Codex, Cursor, Gemini, OpenClaw等)
- Heartbeatシステム: cronベース起動 + アトミックタスクチェックアウト（二重作業防止）
- 予算管理: エージェントごと月次上限、80%警告、100%自動停止
- DBスキーマ52テーブル、プラグインSDK付き
- AAchat示唆: Heartbeatスケジューリング、アトミックチェックアウト、予算コントロール、ゴール階層

### 14. NVIDIA NemoClaw — OpenClawワンコマンドセキュア展開
- 発表: GTC 2026 (2026-03-16)
- GitHub: NVIDIA/NemoClaw (1,900 stars) / NVIDIA/OpenShell (447 stars) / Apache 2.0
- NemoClaw = OpenShell（セキュリティサンドボックス）+ Nemotron（ローカルLLM）
- OpenShell: K3sクラスタをDockerコンテナ内で実行。deny-by-default。4ドメイン制御（ファイル/ネットワーク/プロセス/推論）
- プライバシールーター: 機密データ→ローカルNemotron、それ以外→クラウド
- 対応エージェント: OpenClaw, Claude Code, Codex, Cursor, OpenCode（エージェント非依存）
- `openshell sandbox create -- claude` でClaude Codeをサンドボックス実行
- ポリシー: ロック型（ファイル/プロセス、作成時固定）+ ホットリロード型（ネットワーク/推論）
- Alpha版。RTX 5070 Ti WSL2でセットアップ失敗報告あり
- AAchat示唆: OpenShellでエージェントサンドボックス化、ポリシーas YAML、out-of-process enforcement、マルチエージェントは対象外=AAchatの差別化ポイント

---

# X(Twitter) リサーチ結果 - 2026-03-16

## 検索実施日: 2026-03-16
## 対象期間: 2026-03-09 ~ 2026-03-16

### 報告済みトピック

1. **claude-mem** (GitHub 36K stars) - セッション間の永続メモリプラグイン
2. **Claude Code Review** - Anthropic公式マルチエージェントPRレビュー機能
3. **agency-agents** (40K stars) - 144種のAIエージェント定義集
4. **gstack** (14.8K stars) - Garry Tan作のClaude Codeスキル集
5. **claude-init** - 既存リポジトリから.claude/設定を自動生成
6. **ai-sync** - マルチツール間のコンテキスト同期
7. **rescue-remote** - 死んだリモートセッションからコンテキストを復元
8. **The Mesh Project** - 24/7 Claude Code オーケストレーションシステム
9. **Claude Architect Exam** - Anthropic公式のアーキテクト認定試験
10. **Kaggle金メダル事例** - Claude Code/CodexでKaggle 5位
11. **Anthropic Academy** - 13コース無料公開
12. **Claude Status Updates plugin** - セッションからスタンドアップサマリー生成
13. **Preflight plugin** - コードベーススキャン＋スケーリングシミュレーション
14. **Lumen plugin** - 6オーケストレーター＋18エージェントのPM用プラグイン
15. **GetShitRight plugin** - SaaSアイデア検証プラグイン
16. **Opus 4.6 1Mコンテキスト** - マルチエージェントパイプラインの改善
17. **PlainHub** - MCP Server + CLI対応のGitHub軽量エディタ

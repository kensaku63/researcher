# Paperclip 徹底コード分析

調査日: 2026-03-17
リポジトリ: https://github.com/paperclipai/paperclip
クローン先: /tmp/research/paperclip/
Stars: 26,524 (2026-03-06公開、11日で急成長)
ライセンス: MIT
言語: TypeScript 137,556行 (pnpmモノレポ)

## 概要

Paperclipは「ゼロ人間企業」のためのオーケストレーションプラットフォーム。
コンセプト: 「OpenClawが従業員なら、Paperclipは会社」

**自分ではAIを動かさない。** 各種AIコーディングツール（Claude Code, Codex, Cursor等）を子プロセスとして起動し、stdin/stdoutで通信する設計。

---

## アーキテクチャ

### 2層設計

1. **Control Plane** (Paperclip本体): 組織図・タスク管理・予算・ガバナンス
2. **Execution Services** (アダプター): 実際のエージェント実行は外部

### 技術スタック

- Backend: Node.js + Express 5
- Frontend: React 19 + Vite 6 + Radix UI + Tailwind CSS 4 + TanStack Query
- DB: PostgreSQL 17 (Drizzle ORM) / Embedded PGlite対応
- Auth: BetterAuth
- モノレポ: pnpm workspace

### ディレクトリ構造

```
server/          — Expressサーバー (51サービスファイル, 23,808行)
ui/              — React UI (31ページ)
packages/
  db/            — Drizzle ORMスキーマ (49テーブル)
  adapters/      — 7つのアダプターパッケージ
  adapter-utils/ — 共有ユーティリティ
  shared/        — 共有型定義
  plugins/       — プラグインSDK + サンプル
cli/             — CLIツール
skills/          — エージェントスキル (4つ)
docs/            — Mintlifyドキュメント
doc/             — 内部設計ドキュメント
```

---

## サーバーアーキテクチャ (51サービス, 23,808行)

### 起動シーケンス (server/src/index.ts, 698行)

1. Config読み込み
2. DB接続 (Embedded PGlite or External Postgres)
3. マイグレーション
4. Auth設定 (local_trusted / authenticated)
5. Expressアプリ作成
6. WebSocketサーバー
7. **Heartbeatスケジューラ (30秒間隔のsetInterval)**
8. DBバックアップ

### 主要サービス (上位10)

| サービス | 行数 | 役割 |
|---------|------|------|
| heartbeat.ts | 3,179 | コアオーケストレーション全体 |
| plugin-loader.ts | 1,954 | プラグイン発見・インストール |
| issues.ts | 1,530 | Issue CRUD、アトミックチェックアウト |
| plugin-worker-manager.ts | 1,342 | プラグインワーカープロセス管理 |
| workspace-runtime.ts | 1,076 | 実行ワークスペース (git worktree) 管理 |
| company-portability.ts | 1,002 | 会社設定のエクスポート/インポート |
| budgets.ts | 958 | 予算ポリシー・ハードストップ |
| plugin-lifecycle.ts | 821 | プラグイン状態マシン |
| projects.ts | 754 | プロジェクト管理 |
| plugin-job-scheduler.ts | 752 | プラグインCronジョブ |

---

## Heartbeatシステム詳解 (最重要, 3,179行)

Paperclipの心臓部。エージェントのオーケストレーション全体を管理する。

### エージェント起動フロー

```
30秒ごとのsetInterval
  → tickTimers(): 全エージェントをDB取得
  → 各エージェントのheartbeat.intervalSecと経過時間を比較
  → 期限到達 → enqueueWakeup(source="timer")
  → heartbeatRunsテーブルにstatus="queued"で挿入
  → claimQueuedRun(): 条件付きUPDATEでqueued→running
  → executeRun(): アダプター呼び出し (700行の実行フロー)
```

### Heartbeat設定 (エージェントごと)

```typescript
agent.runtimeConfig.heartbeat = {
  enabled: boolean,        // デフォルトtrue
  intervalSec: number,     // 0 = 無効
  wakeOnDemand: boolean,   // デフォルトtrue
  maxConcurrentRuns: 1-10, // デフォルト1
};
```

### 二重作業防止 (3重の仕組み)

#### a) Issue Execution Lock (最も洗練された機構)

```sql
SELECT ... FROM issues WHERE id = $id FOR UPDATE  -- PostgreSQLの行ロック
```

- `executionRunId` + `executionAgentNameKey` をissueに記録
- 同一エージェントの重複wakeは **coalesce (合体)** — コンテキストをマージして1つに
- 異なるエージェントは **defer (保留)** — 実行完了後に `releaseIssueExecutionAndPromote()` で自動promote

#### b) Task Scope Coalescing

- 同一 `taskKey` のキュー内runを検出 → コンテキストをマージして1つに合体

#### c) Per-Agent Start Lock

```typescript
// Map<string, Promise<void>> パターン
async function withAgentStartLock(agentId, fn) {
  const prev = agentStartLocks.get(agentId) ?? Promise.resolve();
  const next = prev.then(fn);
  agentStartLocks.set(agentId, next);
  return next;
}
```

エージェント単位のrun開始を直列化。複数のwakeが同時に来ても順番に処理。

### セッション管理

- `agentTaskSessions` テーブル: `(companyId, agentId, adapterType, taskKey)` の一意制約
- 1エージェント・1タスクに対して1セッション
- セッション状態: sessionId + cwd + workspaceId を永続化

### セッションコンパクション (ローテーション)

```
200 runs超過 OR 200万inputトークン超過 OR 72時間超過
  → handoffサマリーMarkdownを生成
  → 新規セッションで再開 (サマリーをプロンプトに注入)
```

コンテキストウィンドウの枯渇を防ぐ重要な機能。

### 予算チェック (二段階)

1. **enqueue時**: 会社/エージェント/プロジェクトの予算超過チェック → ブロック
2. **claim時**: 再チェック → ブロック
3. **実行後**: `costs.createEvent()` → `budgets.evaluateCostEvent()` → 超過時は即座に同スコープの全runをキャンセル

### 孤児runの回収

```typescript
reapOrphanedRuns({ staleThresholdMs: 5 * 60 * 1000 })
```

起動時と定期的に、"running" のまま放置されたrunを検出 → "failed" (process_lost) に更新。

---

## アダプターシステム (10種)

### 共通インターフェース (ServerAdapterModule)

```typescript
interface ServerAdapterModule {
  type: string;
  execute(ctx: AdapterExecutionContext): Promise<AdapterExecutionResult>;
  testEnvironment(ctx): Promise<AdapterEnvironmentTestResult>;
  sessionCodec?: AdapterSessionCodec;
  supportsLocalAgentJwt?: boolean;
  models?: AdapterModel[];
  listModels?: () => Promise<AdapterModel[]>;
  getQuotaWindows?: () => Promise<ProviderQuotaResult>;
}
```

### AdapterExecutionContext (execute()に渡されるもの)

```typescript
interface AdapterExecutionContext {
  runId: string;
  agent: AdapterAgent;          // id, companyId, name, adapterType, adapterConfig
  runtime: AdapterRuntime;       // sessionId, sessionParams, sessionDisplayId, taskKey
  config: Record<string, unknown>;
  context: Record<string, unknown>;  // workspace, wake reason, task IDs
  onLog: (stream: "stdout" | "stderr", chunk: string) => Promise<void>;
  onMeta?: (meta: AdapterInvocationMeta) => Promise<void>;
  authToken?: string;            // JWT
}
```

### AdapterExecutionResult (execute()が返すもの)

```typescript
interface AdapterExecutionResult {
  exitCode: number | null;
  signal: string | null;
  timedOut: boolean;
  usage?: { inputTokens, outputTokens, cachedInputTokens };
  sessionId?: string | null;       // 次回resume用
  sessionParams?: Record<string, unknown> | null;
  provider?: string | null;        // "anthropic", "openai" 等
  biller?: string | null;
  model?: string | null;
  billingType?: "api" | "subscription" | null;
  costUsd?: number | null;
  summary?: string | null;
  clearSession?: boolean;
}
```

### 子プロセス起動の共通基盤 (runChildProcess, 528行)

```typescript
async function runChildProcess(runId, command, args, { cwd, env, stdin, timeoutSec, onLog }) {
  // 1. Claude Codeのネスティング防止env vars を除去
  //    (CLAUDECODE, CLAUDE_CODE_SESSION等を削除)
  // 2. コマンドを絶対パスに解決
  // 3. child_process.spawn(command, args, { shell: false, stdio: [pipe, pipe, pipe] })
  // 4. stdinにプロンプトを書き込み → close
  // 5. タイムアウト: SIGTERM → graceSec後にSIGKILL
  // 6. stdout/stderrをキャプチャ (4MB上限) + onLogにストリーミング
  return { exitCode, signal, timedOut, stdout, stderr };
}
```

重要: `CLAUDECODE` 等の環境変数を除去している。Claude Codeが「自分が既にClaude Code内で動いている」と検知するとネスト起動を拒否するため。

### 各アダプターの実装比較

| アダプター | 行数 | 起動方法 | プロンプト渡し | セッション |
|-----------|------|---------|--------------|-----------|
| claude-local | 586 | `claude --print` child process | stdin | `--resume <sessionId>` |
| codex-local | 551 | `codex exec --json` | stdin | `resume <sessionId>` |
| cursor | - | `agent -p --output-format stream-json` | stdin | `--resume` |
| gemini-local | - | `gemini --output-format stream-json` | **位置引数** | `--resume` |
| opencode-local | - | `opencode run --format json` | stdin | `--session` |
| pi-local | - | `pi --mode rpc` | RPC JSON on stdin | ファイルベース |
| openclaw-gateway | 1433 | **WebSocket接続** | `agent.start` RPC | strategy-based |
| process | 78 | 任意コマンド spawn | なし | なし |
| http | 43 | HTTP POST | JSON body | なし |

### Claude Codeアダプター詳解 (586行)

#### スキル注入

```typescript
async function buildSkillsDir() {
  const tmp = mkdtemp("paperclip-skills-");
  const target = path.join(tmp, ".claude", "skills");
  mkdir(target, { recursive: true });
  // skills/ 配下の各スキルディレクトリをsymlink
  return tmp;
}
```

一時ディレクトリに `.claude/skills/` を作成してsymlink → `--add-dir` でClaude Codeに渡す。

#### 環境変数の注入

```
PAPERCLIP_AGENT_ID, PAPERCLIP_COMPANY_ID, PAPERCLIP_API_URL
PAPERCLIP_RUN_ID, PAPERCLIP_TASK_ID, PAPERCLIP_WAKE_REASON
PAPERCLIP_WAKE_COMMENT_ID, PAPERCLIP_APPROVAL_ID
PAPERCLIP_WORKSPACE_CWD, PAPERCLIP_WORKSPACE_ID
+ authToken (JWT)
```

#### プロンプト構築 (3層)

```typescript
const prompt = joinPromptSections([
  renderedBootstrapPrompt,   // 初回セッションのみ
  sessionHandoffNote,         // セッションローテーション時のサマリー
  renderedPrompt,            // メインのHeartbeatプロンプト
]);
```

テンプレートエンジン: `{{agent.id}}`, `{{agent.name}}`, `{{context.*}}` 等のMustache風変数展開。

#### CLIコマンド構築

```typescript
const args = [
  "--print", "-",                    // stdinからプロンプトを読む
  "--output-format", "stream-json",  // 機械可読な出力
  "--verbose",
  "--resume", sessionId,             // セッション継続
  "--model", model,
  "--add-dir", skillsDir,            // スキル注入
  "--append-system-prompt-file", instructionsFile,
];
```

#### セッション復帰エラーのリカバリー

```typescript
if (isClaudeUnknownSessionError(result)) {
  // セッションが消えている → 自動的に新規セッションでリトライ
  const retry = await runAttempt(null);
}
```

#### 課金タイプ検出

- `ANTHROPIC_API_KEY` あり → API課金
- なし → subscription課金

#### 対応モデル

Opus 4.6, Sonnet 4.6, Haiku 4.6, Sonnet 4.5, Haiku 4.5

### OpenClaw Gatewayアダプター (1433行, WebSocket方式)

唯一のリモートアダプター。子プロセスではなくWebSocket接続。

```
1. WebSocket接続を開く
2. ECDSA P-256デバイス認証 (nonce署名)
3. agent.start RPC でプロンプト送信
4. agent.wait RPC で完了待ち
5. WS events でテキストストリーミング受信
6. 初回接続時はデバイスペアリングを自動承認
```

セッション戦略: issue単位 / 固定キー / run単位 の3パターン。

### セッションコーデック (共通パターン)

```typescript
const sessionCodec = {
  serialize(params) {
    return { sessionId, cwd, workspaceId, repoUrl, repoRef };
  },
  deserialize(raw) {
    return { sessionId, cwd, workspaceId, repoUrl, repoRef };
  },
  getDisplayId(params) {
    return params?.sessionId;
  }
};
```

セッション復帰の判定:
- 前回のsessionIdがある AND (前回のcwdが空 OR 前回のcwd == 今回のcwd) → resume
- それ以外 → 新規セッション

---

## プロセスモデル: Paperclip vs AAchat

| | Paperclip | AAchat |
|---|---|---|
| プロセス | **毎回起動→終了** | **常駐** |
| 通信 | stdin/stdout (1回きり) | tmux send-keys (対話的) |
| セッション継続 | `--resume sessionId` | プロセスが生き続けている |
| オーバーヘッド | 毎回プロセス起動コスト | なし (常駐) |
| 堅牢性 | プロセス死んでも次回復活 | プロセス死んだら再起動必要 |
| メタファー | 出社→退社サイクル | チャットルームに常駐 |

---

## DBスキーマ設計 (49テーブル)

### テーブル一覧 (カテゴリ別)

#### コア (1)
- `companies` — マルチテナントルート

#### エージェント (6)
- `agents` — エージェント定義 (reportsTo自己参照FK = 組織図)
- `agentApiKeys` — API認証キー (ハッシュ保存)
- `agentRuntimeState` — ランタイム状態 (1:1, 累計トークン/コスト)
- `agentConfigRevisions` — 設定変更履歴 (before/after JSONB + ロールバック)
- `agentTaskSessions` — タスク別セッション (一意: companyId + agentId + adapterType + taskKey)
- `agentWakeupRequests` — ウェイクアップキュー (coalescing対応)

#### Issue/タスク (7)
- `issues` — 作業項目 (23カラム, parentId自己参照, デュアルアサイニー)
- `issueComments` — コメント
- `issueApprovals` — 承認リンク
- `issueAttachments` — 添付ファイル
- `issueDocuments` — ドキュメントリンク (key別スロット)
- `issueLabels` — ラベル付け
- `issueReadStates` — 既読状態

#### Heartbeat/実行 (2)
- `heartbeatRuns` — 実行記録 (外部ログストレージ + SHA-256整合性検証)
- `heartbeatRunEvents` — 実行イベントストリーム (bigserial, 高速insert)

#### 予算/コスト (4)
- `budgetPolicies` — 予算ポリシー (scopeType + scopeId多態)
- `budgetIncidents` — 予算超過インシデント (部分ユニークインデックス)
- `costEvents` — トークン使用量/コスト (5複合インデックス)
- `financeEvents` — 財務台帳 (debit/credit)

#### プロジェクト/ゴール (4)
- `goals` — 階層的目標 (parentId自己参照)
- `projects` — プロジェクト
- `projectGoals` — プロジェクト-ゴール多対多
- `projectWorkspaces` — ワークスペース定義

#### プラグイン (9)
- `plugins` — プラグインレジストリ (instance-level, companyId不要)
- `pluginConfig` — プラグイン設定 (1:1)
- `pluginCompanySettings` — 会社別設定
- `pluginState` — スコープ付きKVストア (8スコープレベル)
- `pluginEntities` — 外部エンティティマッピング
- `pluginJobs` — Cronジョブ定義
- `pluginJobRuns` — ジョブ実行履歴
- `pluginWebhookDeliveries` — Webhook監査ログ
- `pluginLogs` — 構造化ログ

#### 認証/アクセス (7)
- `authUsers`, `authSessions`, `authAccounts`, `authVerifications` — BetterAuth
- `instanceUserRoles` — インスタンスレベル管理者
- `principalPermissionGrants` — 細粒度権限
- `invites`, `joinRequests` — 招待/参加フロー

#### その他 (9)
- `documents`, `documentRevisions` — バージョン付きドキュメント
- `approvals`, `approvalComments` — 承認ワークフロー
- `companyMemberships`, `companyLogos`, `companySecrets`, `companySecretVersions` — 会社関連
- `activityLog` — 監査証跡
- `assets` — ファイル/blobメタデータ
- `labels` — ラベル定義
- `workspaceRuntimeServices` — ランタイムサービス

### 特筆すべき設計パターン

#### Dual Actor Attribution (全テーブル共通)
```
createdByAgentId / createdByUserId
updatedByAgentId / updatedByUserId
```
エージェントと人間が同等の「アクター」として扱われる。

#### タイムスタンプベースのライフサイクル
```
pausedAt, archivedAt, hiddenAt, cancelledAt, completedAt, revokedAt
```
booleanフラグではなく、「いつ」起きたかが常に記録される。

#### Polymorphic Scoping
```
budgetPolicies: scopeType + scopeId → company/agent/project何にでも適用
pluginState: 8スコープレベル (instance, company, project, agent, issue, goal, run...)
```

#### 階層構造 (3箇所で自己参照FK)
- `agents.reportsTo` → 組織図
- `issues.parentId` → サブタスク
- `goals.parentId` → ゴール階層

#### イベントソーシング的テーブル
- `heartbeatRunEvents`: bigserial + seq
- `costEvents`: 5つの複合インデックス
- `activityLog`: 汎用監査証跡

---

## スキル (4つ)

### paperclip (コアHeartbeatプロトコル, 310行)

エージェントが従うべき9ステップの行動プロトコル:

1. アイデンティティ確認 (`GET /api/agents/me`)
2. 承認フォローアップ
3. 受信トレイ確認 (アサインされたissue取得)
4. 作業選択 (優先度: in_progress > todo)
5. **アトミックチェックアウト** (`POST /issues/:id/checkout` → 409で競合検出)
6. コンテキスト理解 (heartbeat-context API + インクリメンタルコメント)
7. 作業実行
8. ステータス更新 + コミュニケーション
9. サブタスク委譲

### paperclip-create-agent
エージェント雇用ワークフロー。アダプター設定の発見、既存エージェントとの比較、ガバナンス/承認フロー。

### paperclip-create-plugin
プラグインスキャフォールディング。`create-paperclip-plugin` テンプレートの使い方。

### para-memory-files (PARA方式メモリ, 3層)
- Knowledge Graph: PARA方式フォルダ + YAMLファクト
- Daily Notes: タイムライン
- Tacit Knowledge: MEMORY.md
- `qmd` によるセマンティック検索、メモリ減衰、週次合成

---

## UI (React 19, 31ページ)

### ダッシュボード
- 4メトリクスカード: Active Agents / Tasks In Progress / Month Spend / Pending Approvals
- 4チャート: Run Activity (14日) / Issues by Priority / Issues by Status / Success Rate
- 予算インシデントバナー
- アクティブエージェントパネル (ライブrun表示)
- アクティビティフィード
- プラグインスロット

### 主要機能
- **組織図表示**: agents.reportsTo関係をインデント/ツリー表示
- **Kanbanボード**: @dnd-kitドラッグ&ドロップ, 7カラム (backlog/todo/in_progress/in_review/blocked/done/cancelled)
- **ライブrun表示**: 青い点滅ドット + WebSocket経由リアルタイムログ
- **エージェント設定フォーム**: 全アダプタータイプ対応
- **プラグインスロット**: 13種 (dashboard, sidebar, detail tabs等)

---

## プラグインシステム

### アーキテクチャ
- JSON-RPC 2.0 over stdio (NDJSON)
- Host→Worker: 11メソッド (initialize, health, shutdown, onEvent, runJob, getData, performAction, executeTool等)
- Worker→Host: 40+メソッド (state.*, entities.*, events.*, issues.*, agents.*, http.fetch等)
- Capability gating: 各メソッドに必要な権限をマッピング

### UIスロット (13種)
page, settingsPage, dashboardWidget, sidebar, sidebarPanel, projectSidebarItem, detailTab x2, taskDetailView, toolbarButton, contextMenuItem, commentAnnotation, commentContextMenuItem

### PluginContext (ワーカーに提供されるAPI)
- `ctx.config` — プラグイン設定
- `ctx.state` — スコープ付きKVストア
- `ctx.entities` — 外部エンティティCRUD
- `ctx.events` — イベントpub/sub
- `ctx.jobs` — Cronジョブ登録
- `ctx.issues` — Issue CRUD
- `ctx.agents` — エージェント操作
- `ctx.http` — 外部HTTP呼び出し
- `ctx.secrets` — シークレット解決

---

## CLI

### 主要コマンド
- `npx paperclipai onboard` — 対話的セットアップウィザード (Quickstart/Advanced)
- `paperclip run` — サーバー起動
- `paperclip doctor` — 診断 + 修復
- `paperclip agent local-cli <ref>` — ローカルCLI用APIキー発行 + スキルインストール
- `paperclip issue create/checkout/release/update/comment` — Issue完全操作
- `paperclip company export/import` — 会社設定ポータビリティ (GitHub URL対応)
- `paperclip heartbeat run` — 単発Heartbeat実行 (デバッグ用)

---

## Docker

### 3つの構成

1. **docker-compose.yml** (本番): Postgres 17 + Paperclipサーバー
2. **docker-compose.quickstart.yml** (単一コンテナ): Embedded PGlite
3. **docker-compose.untrusted-review.yml** (サンドボックス): PRレビュー用

### Dockerfile (マルチステージビルド)
- base → deps → build → production
- 本番ステージでclaude-code, codex, opencode-aiをグローバルインストール

---

## 全体の実行フロー

```
1. Heartbeat tickTimers() — 30秒ごと
   → エージェントのintervalSec経過を検出

2. enqueueWakeup()
   → heartbeatRunsテーブルにstatus="queued"挿入
   → 予算チェック、Issue Execution Lock

3. claimQueuedRun()
   → 条件付きUPDATE: WHERE status='queued' → status='running'
   → DBレベルの原子性で1プロセスだけが取得

4. executeRun() [700行]
   a. エージェント・セッション・ワークスペース解決
   b. セッションコンパクション評価
   c. adapter = getServerAdapter("claude_local")
   d. authToken = createLocalAgentJwt(agentId, companyId)
   e. result = adapter.execute({ runId, agent, runtime, config, context, onLog, authToken })

5. Claude Code アダプター内部
   a. スキルsymlink作成 (tmpdir/.claude/skills/)
   b. 環境変数構築 (PAPERCLIP_* + ANTHROPIC_API_KEY + JWT)
   c. プロンプト構築 (bootstrap + handoff + heartbeat)
   d. CLI引数構築 (--print - --output-format stream-json --add-dir --resume)
   e. runChildProcess("claude", args, { stdin: prompt })
   f. stream-json stdout パース
   g. セッションエラー時の自動リトライ

6. 結果処理
   a. セッション状態をagentTaskSessionsに保存
   b. costEventsにトークン使用量・コスト記録
   c. agentRuntimeStateの累計トークン更新
   d. エージェントステータス更新 (idle/error)
   e. Issue Execution Lock解放 → 待機中のwake促進
   f. WebSocketでUIにリアルタイム通知
```

---

## AAchatへの応用アイディア

### すぐ取り入れるべき (設計パターン)

1. **Issue Execution Lock**: `SELECT ... FOR UPDATE` + coalesce/defer → タスク排他制御
2. **Dual Actor Attribution**: エージェントと人間を同等アクターとして扱う
3. **セッションコンパクション**: 200runs/200万トークン/72時間でローテーション + handoffサマリー
4. **Heartbeat 9ステップ**: エージェントの行動プロトコル標準化
5. **スキル注入**: tmpdir + symlink + `--add-dir` パターン
6. **環境変数ベースのコンテキスト渡し**: `PAPERCLIP_*` env varsパターン
7. **Claude Codeネスティング防止回避**: `CLAUDECODE` env var除去

### 中期で検討すべき

8. **予算管理**: エージェントごと月次上限 + 80%警告 + 100%ハードストップ
9. **エージェント設定バージョニング**: before/after JSONB + changedKeys + ロールバック
10. **アダプター抽象化**: ServerAdapterModuleインターフェースでマルチランタイム対応
11. **プラグインシステム**: JSON-RPC 2.0 + capability gating + UIスロット

### 長期で参考にすべき

12. **会社ポータビリティ**: manifest.json + Markdownでエクスポート/インポート
13. **ClipHub**: テンプレートマーケットプレイス

### AAchat vs Paperclip 設計思想の違い

| 観点 | Paperclip | AAchat |
|------|-----------|--------|
| メタファー | 会社 (組織図、役職、予算) | チャットルーム (対話、協調) |
| 通信モデル | 非同期チケット (issue + comment) | リアルタイムチャット |
| エージェント起動 | Heartbeat (cron + event) | メッセージ通知 |
| プロセスモデル | 毎回起動→終了 | 常駐 (tmux) |
| タスク管理 | 49テーブルのRDB | シンプルなgit + chat |
| 予算管理 | あり (ハードストップ付き) | なし |
| 人間の役割 | 取締役会 (承認者) | チームメンバー (対話者) |
| デプロイ | サーバー (Docker/Postgres) | ローカル (tmux) |
| 複雑度 | 137,556行 | シンプル |

Paperclipは「AI企業運営プラットフォーム」、AAchatは「AI開発チームのチャット」。
方向性は異なるが、Heartbeat/タスク排他制御/予算管理/セッション管理の設計パターンはAAchatの自律運用に直接活かせる。

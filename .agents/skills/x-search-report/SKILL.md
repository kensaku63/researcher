---
name: x-search-report
description: |
  x-search が返す構造化結果 (JSON) を 1 本の Markdown ファクトレポートに統合する skill。
  ターン内の複数クエリの結果を、投稿の内容傾向で描写型ラベルにグルーピングし、代表投稿付きで提示する。
  仮説・施策・グループ間の因果関係・推測はレポートに混ぜず、考察は x-search-insight に回す。
  templates/_template.md は aachat の kind 雛形を兼ねる (template_policy: create_once)。
---

# x-search-report

X 調査の事実だけを 1 本の Markdown レポートにまとめる skill。`x-search` が返した JSON を agent 自身が読み解いて Markdown に書き起こす。外部 API は呼ばない。

## いつ呼ぶか

- `x-search` が 1 件以上の JSON を返し、ターン内の検索が一区切りしたとき。
- 同一 `purpose` / `language` / `period` の検索結果を 1 本のレポートに統合したいとき。
- 「失敗した検索」「取得できなかった情報」も含め、人間が状況を把握できる形に整えたいとき。

呼ばないでよいケース:

- 計画立案だけが必要 → `x-search-plan`
- 一次取得そのもの → `x-search`
- ファクトレポートから仮説・解釈を作る → `x-search-insight`

## インプット

- 1 ターンに `x-search` を 1 回以上呼んだ結果 (JSON、`schemas/result.schema.json` 準拠)。サブコマンドは `search` / `expand` / `account` / `counts` / `lookup` / `trend` が混在してよい。
- `x-search-plan` skill で確定した検索目的 (`purpose`) 1 つ。1 ターン 1 purpose を厳守する。

## 出力

- Markdown 1 本。`templates/_template.md` のレイアウトに従う。
- 出力先は原則 aachat 共有ドキュメント `aachat/docs/<team>/<project>/<kind>/<id>.md`。
- kind 雛形が未登録なら、まず `aachat/docs/<team>/<project>/<kind>/_template.md` に同梱テンプレートをコピーする。`template_policy: create_once` のため、既存テンプレートは上書きしない。

## 手順

1. **インプットの整合性を確認する**
   - すべての JSON で `purpose` / `language` / `period` が一致すること。
   - 一致しない場合は 1 ターン 1 purpose の前提が崩れているため、レポート生成を止めて `x-search-plan` に戻る。

2. **frontmatter を埋める**
   - `title`: 「<目的> / <主キーワード> / <期間>」を目安に簡潔に。
   - `summary`: 全体サマリの 1〜2 行要約。グループ名や代表数値を含めてよい。仮説・施策は書かない。
   - `status`: 後述の判定ルールで `complete` / `partial` / `failed`。
   - `purpose`: `x-search-plan` で確定した値。
   - `language` / `period_from` / `period_to`: 検索条件と一致させる。
   - `collected_at`: 入力 JSON の `fetched_at` のうち最も遅いもの。UTC・ISO8601 で揃える。
   - `tools_used`: 全 `items[].provenance.tool` と `queries_tried[].tool` の集合 (`bird` / `x-api` / `web`)。

3. **status 判定**
   - `complete`: 「取得できなかった情報」と「失敗した検索」が両方空。
   - `partial`: どちらか一方が空でないが、採用投稿が 1 件以上ある。
   - `failed`: 検索が成立せず採用投稿 0 件。`limitations[].recoverable=false` が支配的なケース。

4. **検索ログをまとめる**
   - `queries_tried[]` を発火順に 1 行 = 1 クエリで並べる。
   - ID は `Q1`, `Q2`, … をターン内通し番号で振る。途中で振り直さない。
   - 列: `ID` / `クエリ` / `手段` / `件数` / `メモ`。
     - `クエリ`: `queries_tried[].query` (raw)。bird と x-api で文字列が違う場合はその行で実際に飛んだ方を採用。`queries_built.differences[]` に意味的な差があれば短いメモを付ける。
     - `手段`: `bird` / `x_api` / `web`。
     - `件数`: `result_count`。
     - `メモ`: `stage` (discovery / collection / single / lookup) や、フォールバック・期間縮退などの注記を 1 行で。
   - 入力 JSON に `search_quality` がある場合は、検索ログの直後に「検索品質メモ」として `coverage_score` / `diversity_score` / `novelty_score` / `contradiction_count` / `notes` を転記する。スコアは検索条件改善の補助であり、事実グループの評価には使わない。
   - 入力 JSON に `next_query_candidates[]` がある場合は、同じく検索ログの直後に「次に試す検索候補」として `kind` / `reason` / `suggested_fields` / `expected_observation` を転記する。施策案やコピー案に変換しない。

5. **投稿を採用 / 不採用に振り分ける**
   - 採用候補は `items[]`。ノイズ機械フィルタは `x-search` 側で済んでいる前提。
   - 採用しない場合は理由を「未分類」または「取得できなかった情報」に回す。
   - 採用順に `post-1`, `post-2`, … の通し番号を振る。**グループをまたいでも連番**。
   - 書き直しで投稿を落とした場合は番号を振り直さず**欠番**にする (引用安定性を最優先)。

6. **投稿を内容傾向でグルーピングする**
   - グループ分けは agent 自身が行う (script に任せない)。
   - グループ数は **3〜7** を目安。
     - 3 未満なら分類せず代表投稿だけを並べる (「投稿グループ」セクションを 1 つに統合)。
     - 7 超なら統合か階層化を検討する。
   - グループ名は **描写型** に限定する。
     - 良い例: 「価格に言及する投稿」「使い方を解説する投稿」「他社製品と比較する投稿」
     - 悪い例: 「優良な投稿」「狙い目セグメント」「成功事例」「インフルエンサー候補」「課題」「機会」(評価語・推測語・施策語が混入)
   - 1 つの投稿が複数グループに該当する場合は **主グループ 1 つに配置**、副グループには ID 参照 (`#post-N`) だけを置く。
   - 各グループには **必ず代表投稿 ID を 1 つ以上** 付ける。代表投稿のないグループはレポートに置かない。
   - グループ間の因果関係や仮説は書かない (`x-search-insight` の責務)。
   - グループ ID は `group-1`, `group-2`, … を記載順に振る。

7. **代表投稿セクションを書く**
   - 1 投稿 = `#### post-N: <短い見出し>` ブロック。見出しは投稿内容を客観的に要約 (評価語禁止)。
   - 必須項目:
     - `クエリ` (`#Q1` など)
     - `URL`
     - `投稿者` (`@handle`)
     - `投稿日時` (UTC ISO8601)
     - `取得手段` (`bird` / `x_api` / `web`)
     - `指標` (`likes=… / reposts=… / replies=… / views=…`、不明は `null`)
     - `採用理由` (`items[].why_selected` を転記。空なら採用しない)
     - `本文` (引用ブロックで原文ママ)
   - 本文は改行・絵文字・記号を変更しない。
   - 長文 (目安 1000 文字超) は冒頭と末尾だけを引用し、中略を `(...略...)` で明示する。

8. **未分類セクション**
   - どのグループにも該当しなかった投稿がある場合のみ書く。0 件なら省略可。
   - 件数と特徴 (事実のみ、推測なし) を 1〜3 行で。

9. **取得できなかった情報 / 失敗した検索 / 次に必要な人間操作**
   - 取得できなかった情報: `limitations[].message` のうち取得失敗・データ欠落に関わるもの。再現条件 (期間、access level 不足、Cookie 切れなど) も短く併記する。
   - 失敗した検索: `queries_tried[]` のうち `result_count = 0` または `limitations[]` が紐づくものを表で。
     - 列: `クエリ` / `手段` / `失敗種別` (`limitations[].code`) / `次の選択肢`。
     - `next_query_candidates[]` に関連する候補がある場合は「次の選択肢」に参照する。
   - 次に必要な人間操作: `next_human_actions[]` をそのまま箇条書きに。重複は除く。

## グルーピングの考え方

- 内容傾向 = 「投稿で何が話されているか」。発言の種類 (不満 / 要望 / 称賛 / 比較対象) は **観察された声** として各グループ内に書き、グループ名そのものには使わない。
- グループ名は人間が読んでも誤解しないように、客体 (何について) と話法 (どう書かれているか) の組み合わせで作る。
  - 客体例: 価格 / 使い方 / 他社比較 / 導入事例 / 学習リソース / トラブル
  - 話法例: 言及する / 解説する / 質問する / 比較する / 体験を共有する / 困難を打ち明ける
- 評価・推測・施策語は禁止: 「優良」「成功」「狙い目」「ターゲット」「インフルエンサー候補」「課題」「機会」「ニーズ」「ペインポイント」など。

## ID 規約

- `Q1`, `Q2`, …: 検索ログの行順。ターン内で振り直さない。
- `group-1`, `group-2`, …: 投稿グループの記載順。
- `post-1`, `post-2`, …: 採用順のレポート全体通し番号。グループをまたいでも連番。
- `next-query-1`, `next-query-2`, …: `next_query_candidates[]` の記載順。次回 `x-search-plan` が参照する。
- 書き直しで投稿を削除した場合は **欠番**にする。引用安定性を最優先する。
- 考察レポート (`x-search-insight`) からは `代表投稿 #post-N`、`投稿グループ #group-N`、`検索 #Q1` で参照する。

## frontmatter スキーマ

`templates/_template.md` の `_aachat.schema` を正本とする。すべて aachat 標準語彙に従う。

- `status` enum: `[complete, partial, failed]`
- `purpose` enum: `[market_research, competitor_research, trend_discovery, influencer_discovery, content_planning, social_marketing_research]`
- `tools_used` items enum: `[bird, x-api, web]` (skill 内では `x_api` を使う JSON とは別語彙。aachat 上のラベルは `x-api`)
- `language` / `period_from` / `period_to` / `collected_at` は省略不可。
  - `period_from` / `period_to` は `YYYY-MM-DD` (date)。
  - `collected_at` は ISO8601 UTC (date-time) で揃える。

## aachat 連携

- `templates/_template.md` は frontmatter schema と本文骨子を同居させた kind 雛形。aachat の `<kind>/_template.md` としてコピーすれば、新規 doc 作成時に schema バリデーションが効く。
- 既存の kind 雛形は `template_policy: create_once` のため上書きしない。差分があれば aachat 側で更新合議を行う。
- 1 ターン = 1 ファクトレポート = 1 shared doc を原則とする。ターンをまたいで追記する場合は frontmatter の `collected_at` を最新化し、`status` を再判定する。

## 単体再利用

この skill ディレクトリは単体で別 agent repo の `.agents/skills/<name>/` / Claude Skills `.claude/skills/<name>/` / plugin `skills/<name>/` に移しても動く。

- 依存スクリプト・外部 API なし。インプットは `x-search` 互換 JSON のみ。
- `templates/_template.md` の frontmatter schema を別 agent で流用する場合、`tools_used` enum や `purpose` enum など X 固有の語彙は移し先で書き換える。

## やらないこと

- 仮説、解釈、グループ間の因果関係、施策提案、投稿文・広告文・クリエイティブ案 (すべて `x-search-insight` または別 agent の責務)。
- 検索の再実行・追加取得 (必要なら `x-search-plan` → `x-search` に戻る)。
- 投稿本文の要約・改変 (代表投稿の本文は引用ブロックで原文ママ)。
- 評価語・推測語・施策語をグループ名に入れること。
- secret 値・取得スクリプトの内部ログをレポートに混入させること。

## 参考

- `docs/agent-designs/x-research-expert/SPEC.md` 「レポート仕様 → ファクトレポート」: グルーピング・ID 規約・本文セクションの正本。
- 同 skill ディレクトリの `templates/_template.md`: aachat kind 雛形を兼ねるテンプレート本体。
- 同 skill ディレクトリの `examples/report.md`: 完成レポート例。
- 隣の skill `x-search/SKILL.md`: インプット JSON のフィールド定義。
- 隣の skill `x-search-insight/SKILL.md`: ファクトレポートから考察を作る後続 skill。

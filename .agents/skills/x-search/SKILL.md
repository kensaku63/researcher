---
name: x-search
description: |
  X（旧 Twitter）の調査を `bird` CLI と X API v2 を使い分けて実行し、ノイズ除去・採用理由付けまで行った構造化結果（JSON）を返す skill。
  キーワード検索、ハッシュタグ検索、アカウント起点、スレッド・返信深掘り、投稿量カウント、URL/ID の現存確認、トレンド抽出に対応する。
  raw response はそのまま返さず、テキスト品質・スパム辞書・自動投稿クライアント・近似重複・author quality・engagement 異常まで機械フィルタを適用し、次に試すべき検索候補も構造化して返す。
disable-model-invocation: true
---

# x-search

X 調査の一次取得を行う skill。`bird` / X API / Web 補助の使い分け、必要 secret の注入、構造化結果の整形、失敗の構造化までを担当する。

agent は X の検索演算子を直接書かず、構造化フィールド (`--keywords` / `--any-of` / `--exclude` / `--hashtags` / `--from-accounts` など) で意図を渡す。スクリプト側で bird と X API それぞれの query 文字列に翻訳する。

## いつ呼ぶか

- `x-search-plan` skill が「次に試す検索条件」を決めたあと、最初の事実取得を行うとき。
- 既知の投稿 ID / URL からスレッド・返信を深掘りするとき。
- 競合・候補アカウントの投稿、メンション、プロフィールを集めるとき。
- クエリ候補の投稿量を時系列で見たいとき。
- 検索結果の URL や ID が現存するか確認したいとき。
- 直近 X 内の話題候補を眺めたいとき。

呼ばないでよいケース:

- 計画立案だけが必要 → `x-search-plan`
- 取得済み JSON から Markdown 事実レポートを書く → `x-search-report`
- ファクトレポートから考察・仮説を作る → `x-search-insight`

## サブコマンド

| サブコマンド | 主用途 | 1 次手段 | フォールバック | 備考 |
|---|---|---|---|---|
| `diagnose` | 認証・access level の事前確認 | `bird check` + X API recent ping (`max_results=10`) | — | 実検索なし。credential 状態のみ返す |
| `search` | キーワード / ハッシュタグ / プロフィール条件での候補取得 | bird search (discovery) → X API Recent Search (collection) の 2 段 | 片方不可ならもう片方のみ。両方不可なら `site:x.com` URL 列挙のみ | メイン |
| `expand` | 既知 post の thread / replies 深掘り | `bird thread` + `bird replies --max-pages N` | X API `conversation_id:<id>` Recent Search | 1 ID ずつ |
| `account` | アカウント起点（投稿・メンション・プロフィール） | `bird user-tweets` + `bird about` + `bird mentions --user` | X API `GET /2/users/by/username` → `/users/:id/tweets` / `/users/:id/mentions` | — |
| `counts` | 期間内の投稿量山見・クエリ候補比較 | X API `GET /2/tweets/counts/recent` (`granularity=hour\|day`) | 不可と明示 | bird に該当機能なし |
| `lookup` | URL / ID の現存確認 | `bird read` | X API `GET /2/tweets?ids=...` | 取得後の整形は最小 |
| `trend` | X 内の話題候補抽出 | `bird news --with-tweets` + `bird trending` | — | X API には対応 endpoint なし |
| `graph` | seed アカウント群とのフォロー重なりで候補を採点・発掘 | `bird following --user` / `bird followers --user` | — | bird のみ。観測可能な重なり数とプロフィール事実だけを返す |

`search` は 1 サブコマンドの内部で「discovery 段（広く速く）→ collection 段（再現可能に）」の 2 段を回す。agent が collection を呼び忘れる事故を防ぐため、別サブコマンドに分けない。

`counts` を `--tool=bird` で呼んだ場合は `limitations[].code = "BIRD_FEATURE_NOT_SUPPORTED"` を返す。

## 共通オプション

```text
--purpose <market_research|competitor_research|trend_discovery
           |influencer_discovery|content_planning|social_marketing_research>
--language <ja|en|zh-Hans|zh-Hant>
--region <JP|US|...>            # bird は無視。X API expansion 用
--period <24h|7d|30d|90d|YYYY-MM-DD..YYYY-MM-DD>
--limit <int>                   # 最終返却件数
--max-fetch <int>               # 内部取得上限
--tool <auto|bird|x_api>        # auto は diagnose 結果で選ぶ
--format <json|markdown>
--debug                         # raw に近い補助情報を一時ファイル出力
```

## `search` 固有オプション

agent は構造化フィールドで意図を渡す。複数値はオプションを繰り返し指定する。

```text
--keywords <str>           # AND 結合される必須語（繰り返し可）
--phrases <str>            # "exact phrase" として AND 結合（繰り返し可）
--any-of <str> [<str> ...] # OR グループ。1 回 = 1 グループ。
                           # `--any-of A B` または `--any-of A,B` で同じ OR 群。
                           # 繰り返しで複数 OR 群: `--any-of A B --any-of C D`
--exclude <str>            # NOT 語（繰り返し可）
--hashtags <str>           # # 付き（繰り返し可）
--from-accounts <str>      # from:<handle>
--to-accounts <str>        # to:<handle>
--mentions <str>           # @<handle>
--include-types <str>      # reply|quote|verified|media|links|images|videos
--exclude-types <str>      # retweet|reply|quote
--min-followers <int>      # followers_count:<min>..
--max-followers <int>      # followers_count:..<max>
--min-likes <int>          # 後段の noise filter で使う
--min-replies <int>
--sort <recency|relevancy|engagement>
--raw-query <str>          # 排他。指定時は構造化フィールド無視
```

`--raw-query` はエスケープハッチ。構造化フィールドで表現できないときだけ使う。

## サブコマンド固有オプション

### `expand`

```text
--id <post_id|url>             # 必須。1 件
--include-thread <bool>        # default true
--include-replies <bool>       # default true
--replies-max-pages <int>      # default 2
--quote-depth <int>            # bird --quote-depth 同等。default 1
```

### `account`

```text
--handle <@name>               # 必須
--include-profile <bool>       # default true
--include-tweets <int>         # default 50
--include-mentions <int>       # default 30
--exclude-types <str>          # retweet|reply
```

### `counts`

`search` の構造化フィールドを共用 + 下記。

```text
--granularity <minute|hour|day>   # X API counts と同じ
```

### `lookup`

```text
--id <post_id|url>             # 必須（繰り返し可）
```

### `trend`

```text
--limit <int>                  # default 20
```

### `graph`

「専門性の高いアカウント（seed）を複数フォローしている人ほど情報感度が高い」という仮説を、観測可能なフォロー関係に落とすサブコマンド。素朴に seed のフォロワー一覧を積集合すると、人気 seed はフォロワーを列挙し切れずレート制限で精度が出ない。そこで方向を逆に取り、「あるアカウントのフォロー先（上限があり完全列挙できる）に seed が何個含まれるか」で重なりを採点する。

2 つのモードが同じ「重なり個数」の考え方を共有する。

- `score`: 既に手元にある候補（`--candidate`）それぞれのフォロー先を取得し、seed 群を何個フォローしているか数える。seed のフォロワーを列挙しないので seed の規模は問わない。x-search の結果や既存 longlist の再ランク・検証に使う。
- `expand`: 各 seed のフォロワーを列挙し、出現回数を集計して重なりとする。新しいアカウントの発掘と採点を 1 パスで行う。フォロワー列挙はニッチな seed（目安フォロワー約5千以下）に限り、ページ上限に当たったら `GRAPH_PARTIAL_COVERAGE` で部分被覆を明示する。

```text
--seed <@handle|id>              # 必須。質の基準となる seed（繰り返し可）。数値は user id として扱う
--candidate <@handle|id>         # score モードで採点する候補（繰り返し可）
--mode <auto|score|expand>       # default auto。--candidate があれば score、なければ expand
--min-overlap <int>              # default 1。seed を何個以上フォローしている候補を返すか
--max-following-pages <int>      # default 10。score: 候補のフォロー先取得のページ上限
--max-follower-pages <int>       # default 10。expand: seed のフォロワー列挙のページ上限
--page-size <int>                # default 100。bird -n（最大 100）
--min-followers <int>            # 候補のフォロワー数下限フィルタ（expand で profile が揃う）
--max-followers <int>            # 候補のフォロワー数上限フィルタ
--limit <int>                    # 返す候補数（重なり数 → フォロワー数の順で降順）
```

`@handle` は内部で `user-tweets -n 1` の `authorId` を使って数値 user id に解決する。直近投稿が見えないアカウントは解決できず `RESULTS_INSUFFICIENT` を返す。`graph` の出力は重なり数とプロフィール事実だけで、「優秀」「狙い目」などの評価は付けない。評価は `x-search-insight` の層で行う。

## ノイズ除去オプション

`search` / `account` / `expand` で共通。purpose default で十分な場合は省略可。

```text
--text-min-length <int>                # default 20。本文長下限
--max-url-ratio <float>                # default 0.7。本文中の URL 占有率上限
--max-hashtag-density <float>          # default 0.5。本文に対するハッシュタグ密度上限
--require-matched-terms <bool>         # default true。matched_terms 0 件の item を除外
--noise-phrases-path <path>            # default: <skill>/knowledge/noise-phrases.<lang>.txt
--disable-noise-phrases                # スパム辞書フィルタを切る
--filter-automated-source <auto|on|off># default auto（competitor_research は off）
--detect-near-duplicates <bool>        # default true
--near-dup-similarity-threshold <float># default 0.85
--min-author-quality <float>           # default purpose 連動
--detect-engagement-anomaly <bool>     # default true
--recommend-excludes <bool>            # default true
```

## 出力 JSON 骨格

`schemas/result.schema.json` を満たす。代表フィールド:

```jsonc
{
  "platform": "x",
  "tool": "search",
  "purpose": "trend_discovery",
  "language": "ja",
  "region": "JP",
  "period": "7d",
  "fetched_at": "2026-05-21T10:00:00Z",

  "credentials": {
    "bird":  { "available": true,  "checked_at": "...", "user": "@example" },
    "x_api": { "available": false, "reason": "401_unauthorized" }
  },

  "queries_built": {
    "bird":  "(\"生成AI\" OR \"LLM\") (勉強法 OR 学習) lang:ja -is:retweet",
    "x_api": "(\"生成AI\" OR \"LLM\") (勉強法 OR 学習) lang:ja -is:retweet",
    "differences": [],
    "recommended_excludes": [
      { "term": "求人", "reason": "off_topic", "evidence_count": 18 }
    ]
  },

  "queries_tried": [
    { "stage": "discovery",  "tool": "bird",  "query": "...", "result_count": 18, "elapsed_ms": 1240 }
  ],

  "items": [
    {
      "url": "https://x.com/example/status/...",
      "source_id": "...",
      "author": { "name": "...", "handle": "@example", "url": "...", "source": null, "followers": 12345 },
      "published_at": "2026-05-20T12:00:00Z",
      "text": "...",
      "metrics": { "likes": 120, "reposts": 30, "replies": 12, "quotes": 4, "views": 8000 },
      "matched_terms": ["生成AI", "勉強法"],
      "why_selected": "直近 7 日で likes 上位かつ reply_to_like_ratio が中央値以上",
      "provenance": { "tool": "bird", "stage": "discovery", "fetched_at": "..." },
      "limitations": []
    }
  ],

  "excluded_summary": {
    "total_excluded": 145,
    "by_reason": [
      { "code": "duplicate_source_id", "count": 8 },
      { "code": "near_duplicate", "count": 42, "cluster_count": 18 },
      { "code": "spam_phrase", "count": 33, "matched_terms": ["拡散希望", "フォロバ"] }
    ]
  },

  "search_quality": {
    "coverage_score": 0.62,
    "diversity_score": 0.71,
    "novelty_score": null,
    "contradiction_count": 2,
    "notes": [
      "採用投稿は複数 author に分散しているが、direct_terms 軸に偏っている",
      "反証語彙を含む投稿が少ないため validation が不足している"
    ]
  },

  "next_query_candidates": [
    {
      "kind": "narrow",
      "reason": "求人ノイズが多く direct_terms 軸の coverage が落ちている",
      "suggested_fields": {
        "exclude": ["求人", "採用"],
        "exclude_types": ["retweet", "reply"],
        "period": "7d"
      },
      "expected_observation": "製品利用・困りごとに関する投稿比率が上がる"
    },
    {
      "kind": "contradict",
      "reason": "既存仮説を弱める発言がまだ少ない",
      "suggested_fields": {
        "any_of": [["不要", "使わない", "代替で十分"]],
        "period": "30d"
      },
      "expected_observation": "対象カテゴリを必要としていない理由が見える"
    }
  ],

  "usage": {
    "x_api_post_reads": 0,
    "bird_calls": 1,
    "cached_hits": 0
  },

  "limitations": [
    {
      "code": "API_TOKEN_MISSING",
      "scope": "collection",
      "message": "X_BEARER_TOKEN 未設定。bird のみで discovery を行った",
      "recoverable": true
    }
  ],

  "next_human_actions": [
    "X Developer Portal で Bearer Token を発行し、aachat env provider に X_BEARER_TOKEN として設定して `aachat up` 後に再実行する"
  ]
}
```

## 失敗 enum（`limitations[].code`）

| code | 意味 | recoverable |
|---|---|---|
| `BIRD_AUTH_MISSING` | `AUTH_TOKEN` / `CT0` 未設定 | true（X API のみで継続可） |
| `BIRD_AUTH_EXPIRED` | Cookie 期限切れ | false |
| `BIRD_QUERY_ID_STALE` | GraphQL query ID 古い | true（`bird query-ids --fresh`） |
| `BIRD_RATE_LIMITED` | Bot 判定・短時間多発 | true |
| `BIRD_FEATURE_NOT_SUPPORTED` | counts など bird に該当機能なし | false |
| `API_TOKEN_MISSING` | `X_BEARER_TOKEN` 未設定 | true（bird のみで継続可） |
| `API_UNAUTHORIZED` | 401 | false |
| `API_FORBIDDEN` | 403。access level 不足など | false |
| `API_RATE_LIMITED` | 429 | true |
| `API_QUOTA_EXCEEDED` | 月間 cap / pay-per-use 上限 | false |
| `API_FULL_ARCHIVE_NOT_AVAILABLE` | full-archive 不可で 7d に縮退 | false |
| `QUERY_TOO_BROAD` | 結果過多 / ノイズ過多 | true（除外語提案あり） |
| `RESULTS_INSUFFICIENT` | 結果過少。seed/候補が解決できない場合も含む | true（条件緩和提案あり） |
| `GRAPH_PARTIAL_COVERAGE` | `graph expand` でフォロワーのページ上限に到達。重なりを過少集計している可能性 | true（よりニッチな seed / `--max-follower-pages` 引き上げ） |
| `BUDGET_NOT_APPLICABLE` | 初版ではコストガード未対応 | — |

`recoverable=true` の場合は agent が自動緩和、`false` の場合は `next_human_actions` を返して human escalation する。

## 必要 secret

値は aachat env provider で管理し、repo / コード / レポート / ログには絶対に含めない。`environment.yaml` には env 名と用途だけを宣言し、スクリプトは session 起動時に注入された標準環境変数だけを読む。

| secret 名 | 用途 | 必須 | 備考 |
|---|---|---|---|
| `AUTH_TOKEN` | bird Cookie 認証 | 任意 | bird を使う場合のみ必要 |
| `CT0` | bird Cookie 認証 | 任意 | bird を使う場合のみ必要 |
| `X_BEARER_TOKEN` | X API v2 | 任意 | X API を使う場合のみ必要 |

未設定の場合は構造化エラー（`BIRD_AUTH_MISSING` / `API_TOKEN_MISSING`）を返し、`next_human_actions` に「aachat env provider に設定して `aachat up` 後に再実行する」を入れる。

## 実行例

### diagnose

```bash
python3 .agents/skills/x-search/scripts/search.py diagnose --format json
```

### search

```bash
python3 .agents/skills/x-search/scripts/search.py search \
  --purpose trend_discovery \
  --language ja \
  --region JP \
  --period 7d \
  --limit 20 \
  --any-of "生成AI" "LLM" \
  --any-of "勉強法" "学習" \
  --exclude 求人 --exclude 採用 \
  --exclude-types retweet \
  --sort recency \
  --tool auto \
  --format json
```

### expand

```bash
python3 .agents/skills/x-search/scripts/search.py expand \
  --id https://x.com/example/status/0000000000000000000 \
  --include-thread true \
  --include-replies true \
  --replies-max-pages 2 \
  --format json
```

### account

```bash
python3 .agents/skills/x-search/scripts/search.py account \
  --handle @competitor_handle \
  --include-profile true \
  --include-tweets 50 \
  --include-mentions 30 \
  --exclude-types retweet \
  --format json
```

### counts

```bash
python3 .agents/skills/x-search/scripts/search.py counts \
  --period 7d \
  --granularity hour \
  --any-of "生成AI" "LLM" \
  --exclude 求人 \
  --format json
```

### lookup

```bash
python3 .agents/skills/x-search/scripts/search.py lookup \
  --id https://x.com/example/status/0000000000000000000 \
  --format json
```

### trend

```bash
python3 .agents/skills/x-search/scripts/search.py trend --limit 20 --format json
```

### graph

```bash
# expand: ニッチな seed 群のフォロワーから、複数 seed をフォローしている人を発掘する
python3 .agents/skills/x-search/scripts/search.py graph \
  --seed @niche_specialist_a \
  --seed @niche_specialist_b \
  --seed @niche_specialist_c \
  --min-overlap 2 \
  --max-follower-pages 10 \
  --min-followers 50 \
  --limit 30 \
  --format json

# score: 既存候補（longlist 等）を seed 群とのフォロー重なりで採点・再ランクする
python3 .agents/skills/x-search/scripts/search.py graph \
  --mode score \
  --candidate @candidate_1 --candidate @candidate_2 \
  --seed @seed_a --seed @seed_b --seed @seed_c \
  --min-overlap 1 \
  --format json
```

出力の `candidates[]` は各候補について `overlap_count`（フォローしている seed の数）、`matched_seeds[]`、`followers_count` / `following_count` / `blue_verified` / `created_at` / `bio` を持つ。`graph_summary` に mode・seed 数・候補プール規模・返却数が入る。これらは事実なので、ファクトレポート（`x-search-report`）の代表候補セクションに転記し、「だから声をかける価値がある」という判断は `x-search-insight` に回す。

## 出力の使い方

- agent は `items[]` を読み、ファクトレポート（`x-search-report` skill）の代表投稿セクションに転記する。
- `excluded_summary.by_reason[]` を見て、ノイズが多すぎる場合 (`QUERY_TOO_BROAD`) には `queries_built.recommended_excludes[]` を次ターンの `--exclude` に加える。
- `search_quality` を見て、採用投稿の偏り、検索軸の薄さ、反証不足を判断する。スコアは絶対評価ではなく、次検索の優先順位付けに使う。
- `next_query_candidates[]` を見て、次ターンの `x-search-plan` で「広げる」「絞る」「反証する」「深掘りする」のどれを行うか決める。
- `limitations[].code` を見て、`recoverable=true` なら自動でフォールバック（bird ↔ X API、期間縮退）、`recoverable=false` なら `next_human_actions` を Markdown に書き出して人間に渡す。
- `queries_built` と `queries_tried` を必ずファクトレポートに残す。再現性のために手段・クエリ・件数・取得日時を明示する。

## 検索価値の評価

`search_quality` と `next_query_candidates[]` は、同じ検索を繰り返さず次の探索に進むための補助情報。script が算出できない項目は `null` にしてよい。

`search_quality` の目安:

- `coverage_score`: 検索軸に対して採用投稿がどれだけ広く分布したか。単一語彙・単一軸に偏るほど低い。
- `diversity_score`: author、URL、投稿タイプ、会話クラスタの分散。特定アカウントや近似重複に偏るほど低い。
- `novelty_score`: 既存レポートとの差分が分かる場合のみ入れる。既存レポートを読めない場合は `null`。
- `contradiction_count`: 反証語彙、否定表現、別解釈を示す採用投稿の件数。仮説検証で特に使う。
- `notes`: 次検索の判断に使える短い観察。施策やコピー案は書かない。

`next_query_candidates[].kind` は次を使う。

- `broaden`: 結果が少なすぎるため、語彙・期間・言語・代替手段を広げる。
- `narrow`: 結果やノイズが多すぎるため、期間・除外語・投稿タイプ・engagement 条件で絞る。
- `validate`: 観察済みの傾向が別語彙・別期間でも出るか確認する。
- `contradict`: 仮説を弱める投稿や別解釈を探す。
- `expand`: 代表投稿の thread / replies / quotes を深掘りする。

## 単体再利用

この skill ディレクトリは単体で別 agent repo の `.agents/skills/<name>/` / Claude Skills `.claude/skills/<name>/` / plugin `skills/<name>/` に移しても動く。

- スクリプトは `${CLAUDE_SKILL_DIR}` から相対参照で `knowledge/` / `schemas/` を読む。未設定の場合は `__file__` 基準で自動解決する。
- secret 名（`AUTH_TOKEN` / `CT0` / `X_BEARER_TOKEN`）は変更しない。値は移し先の aachat env provider から注入する。
- 依存パッケージは `requests` / `python-dateutil` のみ（`pydantic` は使わない）。

## やらないこと

- 投稿の自動保存・長期キャッシュ（24h ローカルキャッシュは将来拡張）。
- DM 送信、ユーザーへの直接連絡。
- 規約違反スクレイピング、レート制限回避、ログイン壁の突破。
- 投稿文・広告文・クリエイティブ案の生成（`x-search-insight` も同様）。
- secret 値のログ出力・レポート出力・debug 出力。

## 参考

- `docs/agent-designs/x-research-expert/SPEC.md`: agent / skill 全体の責務、レポート仕様。
- `docs/agent-designs/x-research-expert/SPEC-script.md`: `search.py` の CLI 体系、入力スキーマ、出力 schema、加工ロジック、auto routing、Phase 別実装方針。
- `docs/agent-designs/x-research-expert/x-research-methods.md`: bird / X API の手段別仕様、検索演算子、quota、failover。
- bird CLI: <https://www.npmjs.com/package/@steipete/bird>
- X API Recent Search: <https://docs.x.com/x-api/posts/recent-search>
- X API Post Counts: <https://docs.x.com/x-api/posts/counts/introduction>

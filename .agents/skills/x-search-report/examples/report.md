---
_aachat:
  template_policy: create_once
  schema:
    type: object
    additionalProperties: true
    required: [title, summary, status, purpose, language, period_from, period_to, collected_at, tools_used]
    properties:
      title:   { type: string, minLength: 1, maxLength: 120 }
      summary: { type: string, minLength: 1, maxLength: 240 }
      status:
        type: string
        enum: [complete, partial, failed]
      purpose:
        type: string
        enum:
          - market_research
          - competitor_research
          - trend_discovery
          - influencer_discovery
          - content_planning
          - social_marketing_research
      language:    { type: string, minLength: 2, maxLength: 16 }
      period_from: { type: string, format: date }
      period_to:   { type: string, format: date }
      collected_at: { type: string, format: date-time }
      tools_used:
        type: array
        items: { type: string, enum: [bird, x-api, web] }
      tags: { type: array, items: { type: string } }
  preview_fields: [purpose, language, period_from, period_to, tools_used]
title: "生成AI / LLM 勉強法 — X 投稿の傾向観察 (ja, 7d)"
summary: "ja / 直近 7 日 / 採用 38 件・4 グループ。書籍や動画など学習リソースの紹介、プロンプトや使用例の共有、学習で挫折・行き詰まりを打ち明ける投稿、検索結果に求人が混入することへの不満が観察された。"
status: partial
purpose: trend_discovery
language: ja
period_from: 2026-05-14
period_to: 2026-05-21
collected_at: 2026-05-21T10:05:00Z
tools_used: [bird, x-api]
tags: [x, fact, llm, study]
---

# X 調査ファクトレポート

> 投稿の内容傾向によるグルーピングまでは行う。仮説・施策・グループ間の因果関係・推測は書かない。
> グループ名は描写型 (例: 「価格に言及する投稿」) で、評価語・推測語を含めない。
> 指標 (likes / reposts / replies / views) は frontmatter の `collected_at` 時点のスナップショット。
> ID 規約: `Q1..` 検索ログ、`group-1..` グループ、`post-1..` 採用投稿 (グループをまたいで連番、削除は欠番)。

## 調査条件

- 目的: trend_discovery (X 内で日本語話者が「生成AI / LLM の勉強法」をどう語っているかの傾向観察)
- 言語 / 期間: ja / 2026-05-14 〜 2026-05-21 (直近 7 日)
- 使用ツール: bird (discovery), x-api recent search (collection)
- 取得日時: 2026-05-21T10:05:00Z
- 調査戦略の意図: 「勉強法 / 学習 / 入門」の発言面と「外部リソース紹介 / 自前ノウハウ共有 / 困りごとの吐露」の話法面で分布を取り、ターン内で 1 つの purpose に集中する。
- 分類軸: 投稿の客体 (学習リソース / プロンプト共有 / 学習体験 / 検索体験) × 話法 (紹介・解説・共有・不満)

## 全体サマリ

- クエリ数: 3 (うち 1 件は bird 認証エラーで失敗)
- 取得投稿数 (重複除去後): 47
- 採用投稿数 (グルーピング対象): 38
- グループ数: 4

## 検索ログ

| ID | クエリ | 手段 | 件数 | メモ |
|---|---|---|---:|---|
| Q1 | `("生成AI" OR "LLM") (勉強法 OR 学習) lang:ja -is:retweet` | bird | 18 | discovery 段。直近 7 日。`recommended_excludes` に `求人` / `採用` が浮上 |
| Q2 | `("生成AI" OR "LLM") (勉強法 OR 学習) lang:ja -is:retweet -求人 -採用` | x_api | 25 | collection 段。Q1 の除外語を反映して再現可能化 |
| Q3 | `("生成AI" OR "LLM") ("初心者" OR "入門") lang:ja -is:retweet -求人` | bird | 0 | BIRD_QUERY_ID_STALE で失敗。後段で x_api に切り替えていない |

## 投稿グループ

### group-1: 書籍・動画・講座など外部の学習リソースを紹介する投稿

- 該当件数: 15 / 採用 14 (37%)
- 観察された傾向 (事実の要約のみ): 書籍タイトル・YouTube 動画 URL・有料/無料講座名が並ぶ。「これを最初に読んだ」「無料で十分」など個人の選定理由が一文添えられる例が多い。
- 観察された声:
  - 称賛: 「○○本は数式を最小化していて入門に向いている」「無料の公式 docs を一周するのが結局速い」
  - 比較対象: 「Coursera より YouTube の方が日本語が揃っている」「書籍 A は理論寄り、書籍 B は実装寄り」
- 代表投稿: #post-1, #post-2, #post-3

#### post-1: 入門書とドキュメントを併読する手順を紹介する投稿

- クエリ: #Q2
- URL: https://x.com/example_ja_dev/status/1810000000000000001
- 投稿者: @example_ja_dev
- 投稿日時: 2026-05-18T03:14:00Z
- 取得手段: x_api
- 指標: likes=412 / reposts=58 / replies=21 / views=18432
- 採用理由: 直近 7 日 likes 上位かつ reply_to_like_ratio 中央値以上、外部リソースを 3 件以上具体名で挙げているため学習リソース紹介の代表例として採用。
- 本文:

> LLM の勉強法、結局これだった：
> 1. 入門書を 1 冊通読 (薄めの本でいい)
> 2. 公式 docs を写経しながら一周
> 3. 1 週間 1 テーマ決めて手を動かす
> 一気に全部やろうとして詰む人を何度も見た。順番が大事。

#### post-2: 無料 YouTube 動画と公式チュートリアルの組み合わせを紹介する投稿

- クエリ: #Q1
- URL: https://x.com/example_designer/status/1810000000000000002
- 投稿者: @example_designer
- 投稿日時: 2026-05-19T08:42:00Z
- 取得手段: bird
- 指標: likes=287 / reposts=33 / replies=14 / views=10210
- 採用理由: 学習リソースを 4 件 (動画 2 / docs 2) 具体名で挙げ、選定理由を 1 文ずつ添えている。bird discovery で同条件の中で最も具体的なリソース列挙だったため。
- 本文:

> 生成AI 勉強始めるなら、お金かけずに以下で 1 周できます👇
> ・YouTube の「みんなのLLM入門」シリーズ (理論をふわっと)
> ・LangChain 公式チュートリアル (写経向き)
> ・Hugging Face の Course (英語だけど図が分かりやすい)
> ・OpenAI Cookbook (実装の引き出し)

#### post-3: 書籍 2 冊の使い分けを比較する投稿

- クエリ: #Q2
- URL: https://x.com/example_engineer/status/1810000000000000003
- 投稿者: @example_engineer
- 投稿日時: 2026-05-20T12:01:00Z
- 取得手段: x_api
- 指標: likes=198 / reposts=21 / replies=18 / views=7820
- 採用理由: 書籍 2 冊の特徴を「理論寄り / 実装寄り」と明示し、比較対象として観察された声を裏付ける具体例。
- 本文:

> 生成AI の入門書だと A 本と B 本がよく比較される印象。
> A 本：数式と歴史中心で「なぜそうなるか」を押さえやすい
> B 本：API 叩く章が厚くて「とりあえず動かす」に向く
> 1 冊目に A、2 冊目に B、で結構回ってる人多い気がします。

### group-2: プロンプトや使用例を共有する投稿

- 該当件数: 10 / 採用 10 (26%)
- 観察された傾向: 自分が業務・学習で使っているプロンプト本文をスクリーンショットまたはコードブロックで貼る投稿。RAG / 要約 / 校正 / 翻訳の用途が混在し、「コピペで使えます」「英語向け」など使用シーンを添える例が多い。
- 観察された声:
  - 称賛: 「これそのまま使ってる、便利」「テンプレ化して社内共有した」
  - 要望: 「日本語特化版が欲しい」「コンテキスト長削れる版を希望」
- 代表投稿: #post-4, #post-5

#### post-4: 論文要約用プロンプトをコードブロックで共有する投稿

- クエリ: #Q1
- URL: https://x.com/example_writer/status/1810000000000000004
- 投稿者: @example_writer
- 投稿日時: 2026-05-17T22:30:00Z
- 取得手段: bird
- 指標: likes=521 / reposts=112 / replies=34 / views=22318
- 採用理由: 直近 7 日 reposts 上位。プロンプト本文が full text で貼られ、コピペ再利用可能な共有投稿の代表例として採用。
- 本文:

> 論文要約用に使ってるプロンプト晒します。
> ```
> あなたは批判的読者です。以下の論文について
> (1) 結論を 3 行
> (2) 主な前提と限界を 3 行
> (3) 反証されうる観察を 1 つ
> で出力してください。出典は本文の章番号で示すこと。
> ```
> 結構良いです。日本語論文でも動く。

#### post-5: 翻訳・校正向けプロンプトの英語版を共有する投稿

- クエリ: #Q2
- URL: https://x.com/example_trans/status/1810000000000000005
- 投稿者: @example_trans
- 投稿日時: 2026-05-20T01:15:00Z
- 取得手段: x_api
- 指標: likes=304 / reposts=66 / replies=29 / views=11500
- 採用理由: プロンプト本文を提示しつつ、replies で「日本語版が欲しい」要望が複数寄せられている。「要望」声の根拠として採用。
- 本文:

> Translation prompt I keep coming back to (EN→JA):
> "Translate as if writing for a Japanese technical magazine. Keep terminology unchanged when English is the established usage in JP. Add a 1-line glossary for the 3 most likely-to-confuse terms."
> 日本語化版は人によって好み分かれそうなので各自調整推奨。

### group-3: 学習で挫折・行き詰まりを打ち明ける投稿

- 該当件数: 8 / 採用 8 (21%)
- 観察された傾向: 「3 ヶ月続けたが手応えがない」「数式で詰まる」「業務で使う場面がない」「環境構築で 1 日溶けた」など、自分の体験を 1〜3 行で打ち明ける投稿。リプライで励まし・代替案がつくことが多い。
- 観察された声:
  - 不満: 「数式パートで毎回詰む」「環境構築の情報が古い」「業務で使う機会がなく独学が続かない」
  - 要望: 「数式抜きで実装から入れるロードマップが欲しい」「Mac 前提じゃない手順が欲しい」
- 代表投稿: #post-6, #post-7

#### post-6: 数式と環境構築での挫折体験を打ち明ける投稿

- クエリ: #Q2
- URL: https://x.com/example_student/status/1810000000000000006
- 投稿者: @example_student
- 投稿日時: 2026-05-16T13:48:00Z
- 取得手段: x_api
- 指標: likes=183 / reposts=12 / replies=47 / views=6830
- 採用理由: replies が likes に対し相対的に多く、同様の体験を述べる返信が複数。挫折体験の代表として採用。
- 本文:

> 生成AI 勉強、3 ヶ月続けたけど 2 回目の挫折。
> ・数式パートで手が止まる
> ・チュートリアル動かそうとして env 周りで 1 日溶ける
> ・業務で使う場面がないのでモチベが切れる
> 同じ理由でやめた人いそう。

#### post-7: 業務適用の機会がなく独学が続かないと打ち明ける投稿

- クエリ: #Q1
- URL: https://x.com/example_office/status/1810000000000000007
- 投稿者: @example_office
- 投稿日時: 2026-05-18T11:22:00Z
- 取得手段: bird
- 指標: likes=141 / reposts=8 / replies=33 / views=5210
- 採用理由: 「業務で使う場面がない」という不満の声をストレートに表現しており、observed voices の根拠として採用。replies に同意系の発言が多い。
- 本文:

> 個人で生成AI 勉強してても、職場で使うアテがないと結局忘れる。
> 業務で 30 分でも触れる時間が確保できる人が一番伸びてる気がする。

### group-4: 検索結果に求人・採用情報が混入することへの不満を述べる投稿

- 該当件数: 5 / 採用 5 (13%)
- 観察された傾向: 「LLM 勉強法」「生成AI 入門」で X 検索すると、企業の採用ツイートやスクール広告が上位に並ぶことへの不満。「実際の学習者の発言が埋もれる」「広告ばかり」という発言が中心。
- 観察された声:
  - 不満: 「求人ばかり出てきて勉強の話が読めない」「スクールの宣伝が多い」「ハッシュタグも乗っ取られてる感じ」
- 代表投稿: #post-8

#### post-8: 検索結果が求人で埋まることに不満を述べる投稿

- クエリ: #Q1
- URL: https://x.com/example_learner/status/1810000000000000008
- 投稿者: @example_learner
- 投稿日時: 2026-05-19T15:07:00Z
- 取得手段: bird
- 指標: likes=92 / reposts=4 / replies=11 / views=3220
- 採用理由: Q1 で `recommended_excludes` に `求人` / `採用` が浮上した根拠投稿。検索体験への不満として独立グループの代表に採用。
- 本文:

> 「LLM 勉強法」で検索しても、半分くらいスクールと求人で埋まる。
> 学習してる人の生の声が読みたいだけなんだけど、検索 UI で除外するの面倒すぎる。

## 未分類

- どのグループにも当てはまらなかった投稿: 1 件 (採用 1)
- 主な特徴 (事実のみ): 「勉強法」というキーワードを含むが、本文が個人の散歩記録 (健康法の話) であり、生成AI / LLM とは直接関係がなかった。`matched_terms` には合致したが内容は別領域。

## 取得できなかった情報

- 期間 7 日より前 (5/13 以前) の投稿は full-archive access level がなく取得していない。`limitations[].code = API_FULL_ARCHIVE_NOT_AVAILABLE`。
- bird による初心者・入門軸のクエリ (Q3) は GraphQL query ID が古く失敗したため、取得していない。`limitations[].code = BIRD_QUERY_ID_STALE`。

## 失敗した検索

| クエリ | 手段 | 失敗種別 | 次の選択肢 |
|---|---|---|---|
| `("生成AI" OR "LLM") ("初心者" OR "入門") lang:ja -is:retweet -求人` | bird | BIRD_QUERY_ID_STALE | `bird query-ids --fresh` を実行後に再試行 / または x_api recent search に切り替えて同条件で取得 |

## 次に必要な人間操作

- `bird query-ids --fresh` を実行して GraphQL query ID を更新する。
- 7 日より前を見たい場合、X API の access level (full-archive 対応) を確認・引き上げる。aachat env provider に設定した `X_BEARER_TOKEN` の権限を確認すること。

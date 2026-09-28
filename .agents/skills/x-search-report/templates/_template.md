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
title: ""
summary: ""
status: complete
purpose: market_research
language: ja
period_from: ""
period_to: ""
collected_at: ""
tools_used: []
tags: [x, fact]
---

# X 調査ファクトレポート

> 投稿の内容傾向によるグルーピングまでは行う。仮説・施策・グループ間の因果関係・推測は書かない。
> グループ名は描写型 (例: 「価格に言及する投稿」) で、評価語・推測語を含めない。
> 指標 (likes / reposts / replies / views) は frontmatter の `collected_at` 時点のスナップショット。
> ID 規約: `Q1..` 検索ログ、`next-query-1..` 次検索候補、`group-1..` グループ、`post-1..` 採用投稿 (グループをまたいで連番、削除は欠番)。

## 調査条件

- 目的:
- 言語 / 期間:
- 使用ツール:
- 取得日時:
- 調査戦略の意図:
- 分類軸:

## 全体サマリ

- クエリ数:
- 取得投稿数 (重複除去後):
- 採用投稿数 (グルーピング対象):
- グループ数:

## 検索ログ

| ID | クエリ | 手段 | 件数 | メモ |
|---|---|---|---:|---|
| Q1 |  |  |  |  |

## 検索品質メモ

- coverage_score:
- diversity_score:
- novelty_score:
- contradiction_count:
- notes:

## 次に試す検索候補

| ID | kind | reason | suggested_fields | expected_observation |
|---|---|---|---|---|
| next-query-1 |  |  |  |  |

## 投稿グループ

### group-1: <描写型のグループ名>

- 該当件数: N / 採用 M (X%)
- 観察された傾向 (事実の要約のみ、1〜2 行):
- 観察された声 (本文や返信に出てきた発言を箇条書き、該当なしの項は省略):
  - 不満:
  - 要望:
  - 称賛:
  - 比較対象:
- 代表投稿: #post-1, #post-2

#### post-1: <投稿の短い見出し>

- クエリ: #Q1
- URL:
- 投稿者: @handle
- 投稿日時:
- 取得手段:
- 指標: likes=… / reposts=… / replies=… / views=…
- 採用理由:
- 本文:

> 投稿本文を引用ブロックで原文ママ

## 未分類

- どのグループにも当てはまらなかった投稿: N 件
- 主な特徴 (事実のみ):

## 取得できなかった情報

-

## 失敗した検索

| クエリ | 手段 | 失敗種別 | 次の選択肢 |
|---|---|---|---|

## 次に必要な人間操作

-

---
_aachat:
  template_policy: create_once
  schema:
    type: object
    additionalProperties: true
    required:
      - title
      - summary
      - status
      - source_fact_report
      - purpose
      - language
      - period_from
      - period_to
      - collected_at
      - written_at
    properties:
      title:   { type: string, minLength: 1, maxLength: 120 }
      summary: { type: string, minLength: 1, maxLength: 240 }
      status:
        type: string
        enum: [complete, partial, blocked]
      source_fact_report: { type: string, minLength: 1 }
      purpose:
        type: string
        enum:
          - market_research
          - competitor_research
          - trend_discovery
          - influencer_discovery
          - content_planning
          - social_marketing_research
      language:     { type: string, minLength: 2, maxLength: 16 }
      period_from:  { type: string, format: date }
      period_to:    { type: string, format: date }
      collected_at: { type: string, format: date-time }
      written_at:   { type: string, format: date-time }
      tags: { type: array, items: { type: string } }
  preview_fields:
    - purpose
    - source_fact_report
    - collected_at
    - written_at
title: ""
summary: ""
status: complete
source_fact_report: ""
purpose: market_research
language: ja
period_from: ""
period_to: ""
collected_at: ""
written_at: ""
tags: [x, insight]
---

# X 調査 考察・仮説レポート

> ファクトレポートだけをインプットにし、外部知識や新規取得は持ち込まない。
> 各記述に `#group-N` / `#post-N` / `Q#N` の根拠引用と確信度 (low / medium / high) を必ず付ける。
> 施策提案・投稿文・広告文・クリエイティブ案は書かない。

## 前提

- 参照ファクトレポート: <相対パスまたはタイトル>
- ファクトレポートの取得日時 (`collected_at`):
- 調査目的 (`purpose`):
- 調査範囲 (言語 / 期間):
- 使用ツール (`tools_used`):
- 投稿グループ一覧 (id / 描写型ラベル / 該当件数):
  - `#group-1`:
  - `#group-2`:

## 解釈

### 1. <観察された事実の短い見出し>

- 観察された事実 (要約):
- 根拠: `#group-N` / 代表投稿 `#post-N` / 観察された声 (不満|要望|称賛|比較対象) - `#group-N`
- 想定される意味 (解釈):
- 確信度: low | medium | high
- 確信度の理由:
- 反証材料の可能性:

### 2. <観察された事実の短い見出し>

- 観察された事実 (要約):
- 根拠:
- 想定される意味:
- 確信度:
- 確信度の理由:
- 反証材料の可能性:

## 仮説

### 仮説 1: <反証可能な短い見出し>

- 仮説の主語と文脈 (誰が・どんな状況で):
- 仮説の内容 (1 主張):
- 根拠とする事実: `#group-N` / `#post-N` (URL・指標・採用理由とあわせて引用)
- 確信度: low | medium | high
- 確信度の理由:
- 反証されうる観察 (この観察が出れば仮説は棄却):

### 仮説 2: <反証可能な短い見出し>

- 仮説の主語と文脈:
- 仮説の内容:
- 根拠とする事実:
- 確信度:
- 確信度の理由:
- 反証されうる観察:

## リスク・懸念

- サンプル偏り (特定アカウント / bot / 自動投稿クライアントへの集中):
- 言語・地域による偏り:
- 期間による偏り (`period_from` / `period_to` のスナップショット性):
- 誤読しやすい点 (皮肉・引用 RT・否定形・文脈反転):
- 反証材料の可能性 (同じ事象の別解釈):
- グループ間の混在 (同一投稿が複数グループにまたがる場合):

## 次に調べるべき論点

### 論点 1: <短い見出し>

- 論点:
- 想定される検索クエリ案 (言語・期間・絞り込み軸を含む):
- 期待する確認内容 (どんな観察が出れば論点が解消するか):
- 次ターンで使う skill: `x-search-plan` / `x-search` / `x-search-report` / `x-search-insight`

### 論点 2: <短い見出し>

- 論点:
- 想定される検索クエリ案:
- 期待する確認内容:
- 次ターンで使う skill:

## 不足している事実

- ファクトレポートに含まれず、本レポートで考察を確定できなかった事実:
- その事実があれば確信度を上げられる解釈 / 仮説:

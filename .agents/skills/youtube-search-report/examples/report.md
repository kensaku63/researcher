# YouTube 調査ファクトレポート

## 調査条件

- 目的: trend_discovery
- 言語: ja
- 地域: JP
- 期間: 30d
- 使用 endpoint: search.list / videos.list / channels.list
- 取得日時: 2026-06-06T00:00:00Z
- quota 消費見込み: 102

## 検索ログ

| クエリ | endpoint | 件数 | quota | メモ |
|---|---|---:|---:|---|
| 生成AI 勉強法 | search.list | 42 -> 18 | 100 | 候補取得 |
| 42 video ids | videos.list | 42 -> 42 | 1 | 指標補完 |
| 18 channel ids | channels.list | 18 -> 18 | 1 | チャンネル補完 |

## 代表動画・チャンネル

### 1. 生成AIの勉強法を30日で見直す

- URL: https://www.youtube.com/watch?v=exampleid01
- チャンネル: Example Channel
- 公開日: 2026-05-20T00:00:00Z
- 指標 (views / likes / comments / subscribers): 120000 / 3200 / 180 / 45000
- 採用理由: order=date で直近公開。再生12万、コメント180、同一チャンネル制限を通過。
- 説明文要約: 説明文要約。関連語、対象者、動画構成を300文字以内で保持する。
- 制限: なし

## コメント・反応の事実

- コメントは未取得。

## Trends の事実

- Trends は未取得。

## Transcript の事実

- Transcript は未取得。

## 取得できなかった情報

- なし。

## 失敗した検索

- なし。

## 次に必要な人間操作

- なし。
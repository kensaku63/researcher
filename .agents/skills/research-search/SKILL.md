---
name: research-search
description: 論文・先行研究（arXiv）、YouTube動画、X投稿のキーワード検索を行う。検索候補と出典URLを少量取得するための最小スクリプト。
---

# 検索

このSkillの `scripts/search.py` をPython 3で実行する。追加ライブラリ不要。
aachatでは正本を次のように呼べる。それ以外では読み込んだSkillの場所を基準にscriptを解決する。

```bash
python3 "$AA_AGENT_DIR/.agents/skills/research-search/scripts/search.py" paper 'all:electron' --limit 5
python3 "$AA_AGENT_DIR/.agents/skills/research-search/scripts/search.py" youtube '生成AI 活用' --limit 5
python3 "$AA_AGENT_DIR/.agents/skills/research-search/scripts/search.py" x '生成AI lang:ja -is:retweet' --limit 5
```

- `paper`: arXivのみ。キー不要。英語の検索語を推奨。`ti:`、`au:`、`all:`やAND/ORを利用可能。連続実行は3秒以上空ける。
- `youtube`: YouTube Data API v3の動画検索。`YOUTUBE_API_KEY`が必要。Google Cloud側で同APIを有効にしたキーを使う。
- `x`: X API v2の直近7日間の投稿検索。利用権限・残高のある`X_BEARER_TOKEN`が必要。API仕様上、取得は最低10件で、指定件数までを返す。

環境変数名はagent repoの `environment.yaml` に宣言済み。値はSessionへ環境変数として注入し、repoや会話に保存しない。`.env`の自動読み込みはしない。未設定なら変数名を伝える。キー未設定のサービスを実検索済みとは扱わない。

成功時はstdoutにJSON（`source`, `query`, `fetched_at`, `count`, `items`）、終了コード0。0件も正常な結果。失敗時はstderrに診断、非0で終了する。HTTP 401/403では認証・利用権限、429では上限を確認し、連続再試行しない。HTTP 406は検索結果0件ではなく取得失敗として報告する。

1回の実行で1リクエスト、1ページのみ。`--limit`は1〜50（既定5）。APIのクォータ・料金が発生しうる。自動ページ送り、字幕・本文取得、複数ソース横断、ノイズ除去は初版には含めない。実検索で必要性が分かってから追加する。

結果を伝えるときはURL・検索語・取得日時を根拠にする。論文のabstract、動画のdescription、投稿本文を区別し、本文を読んだ・動画を視聴したとは扱わない。arXiv収録だけで査読済みとは判断しない。

## 参照元

参考repoの取得手段・環境変数名を参考に、最小の検索処理として新規実装した。

- https://github.com/kensaku63/paper-search （論文ソースのうちarXivだけを採用）
- https://github.com/kensaku63/youtube-research （YouTube Data API）
- https://github.com/kensaku63/x-research-expert2 （X API recent search）
- https://info.arxiv.org/help/api/user-manual.html
- https://developers.google.com/youtube/v3/docs/search/list
- https://docs.x.com/x-api/posts/search-recent-posts

初版の動作確認（2026-09-28）: arXiv `all:electron` / 2件は取得成功。別クエリではHTTP 406も観測したため、任意クエリの安定動作は未確認。YouTube・Xはキー未設定時の失敗とサンプル応答の整形のみ確認済みで、実API検索は環境変数設定後に検証する。

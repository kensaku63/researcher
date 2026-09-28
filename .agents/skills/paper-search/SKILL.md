---
name: paper-search
description: |
  論文を探す・読む・要約する依頼が来たら起動する。
  発動シグナル: 「論文を探して」「文献調査」「related work」「先行研究」「arXiv で」
  「PubMed で」「DOI から本文」「abstract をまとめて」「surveys on …」
  「reproduce this paper」「最新の <topic> 研究」「systematic review」。
---

# paper-search skill

[openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) 同梱の `paper-search` CLI を Bash から直接叩き、arXiv / PubMed / bioRxiv / medRxiv / Semantic Scholar / Crossref / OpenAlex / Europe PMC / IACR ePrint / dblp / Zenodo / HAL / SSRN / Unpaywall ほか 20+ ソースを横断検索する。MCP server は使わない。

## CLI 早見表

```bash
paper-search sources                                  # 利用可能ソース一覧
paper-search search "<query>" [opts]                  # 横断検索（重複排除済み JSON）
paper-search download <source> <paper_id> [-o dir]    # PDF 取得
paper-search read     <source> <paper_id> [-o dir]    # PDF 取得 → 本文テキスト
```

`search` のオプション:

- `-n, --max-results N` — 各ソース最大 N 件（default 5）
- `-s, --sources arxiv,pubmed,...` — カンマ区切り or `all`（default all）
- `-y, --year 2020-2024` — Semantic Scholar の年フィルタ（他ソースには無視される）

出力は常に JSON。`jq` でフィルタする:

```bash
paper-search search "diffusion models" -s arxiv,semantic -n 10 \
  | jq '.papers[] | {title, authors, year: .published_date[:4], source, url, doi}'
```

## いつ何を呼ぶか

| やりたいこと | コマンド | 補足 |
|---|---|---|
| 主題を投げて広く探す | `paper-search search "<query>" -n 10` | 全ソース並列・重複排除。最初の一手 |
| ソース絞り込み | `paper-search search "<query>" -s arxiv,semantic -n 20` | 「臨床→pubmed,europepmc」「ML→arxiv,semantic」 |
| DOI / arXiv ID から本文 | `paper-search read <source> <id>` | テキスト抽出まで一発 |
| PDF だけ落とす | `paper-search download <source> <id>` | 保存パスは `-o` |
| 利用可能ソース確認 | `paper-search sources` | env / API key 状況で増減する |

## 検索手順

1. **問いを 1 文に整理**: ユーザーの問いから「主題・期間・分野・除外条件」を抜き出す。曖昧なら 1 回だけ聞き返す
2. **クエリ展開**: 専門用語の言い換え・略語・著者名を 2〜3 個用意する（例: `BERT` ↔ `Bidirectional Encoder Representations from Transformers`）
3. **広く `paper-search search ... -n 10` → 絞り込み**: relevance を見てソース別に深掘り（`-s arxiv` など）
4. **重複・年代・ジャーナルでフィルタ**: preprint vs published、retraction の有無を確認。`jq` で year / source で絞る
5. **本文要約が必要なら `paper-search read`**: テキスト抽出済みを受け取って要約
6. **出力フォーマット**: タイトル / 著者（First+et al.）/ 年 / venue / DOI or arXiv ID / 1-2 文の要約 / link を箇条書きで返す。出典のソース名と取得日を必ず添える

## ソース別の使い分け（rough）

- **arxiv**: CS / Physics / Math / Stat の preprint。最新性◎、peer review なし
- **pubmed / pmc / europepmc**: 生物医学。MeSH 検索強い
- **biorxiv / medrxiv**: 生命・医学の preprint
- **semantic**: 横断・引用ネットワーク。abstract と TLDR 充実
- **crossref / openalex**: メタデータ正本。DOI 確定や引用情報
- **iacr**: 暗号
- **dblp**: CS 会議・著者ベース
- **unpaywall**: OA 版 PDF へ誘導
- **core / base / doaj / zenodo / hal / ssrn**: OA 横断

## 引用・出典のルール

- 取得した論文情報は CLI 結果由来であることを明示。タイトル / 著者 / DOI を勝手に補完しない
- abstract や本文を要約する時は、原文の主張と要約の境界を明確にする
- 該当なし・取得失敗は素直に報告する。検索結果を捏造しない

## API key / env

`environment.yaml` で名前だけ宣言。値はローカル env に export する:

```bash
export PAPER_SEARCH_MCP_UNPAYWALL_EMAIL='you@example.com'
export PAPER_SEARCH_MCP_CORE_API_KEY='...'                       # 任意
export PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY='...'           # 任意
```

未設定でも大半のソースは動くが、`paper-search` 起動時に「Unpaywall fallback will be skipped」等の warning が stderr に出る（無害）。JSON だけ欲しい時は `2>/dev/null` を付ける。

## トラブルシュート

- `paper-search: command not found` → `pip show paper-search-mcp` で導入確認。未導入なら `pip install 'paper-search-mcp @ git+https://github.com/openags/paper-search-mcp.git'`（PyPI 版 0.1.4 は CLI が含まれない）
- ソース側 rate limit → 別ソースに切り替えるか待つ。retry 連打しない
- 巨大 JSON で context を食う → `paper-search ... | jq '.papers | map({title,doi,url,source})'` で圧縮

## 参考

- [openags/paper-search-mcp (GitHub)](https://github.com/openags/paper-search-mcp)
- CLI 本体: `paper_search_mcp/cli.py`

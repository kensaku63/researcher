# LLM事業×推定時価総額 統合の道具

content-blog の統合版（docs/research/llm-business-valuation-20261026.md）と詳細doc 9本・参考表docを作り直すための道具。R15反映後の状態（2026-10-31）。

- `master_r15.json`：全行のデータ（本表＋参考表、`appendix`／`deleted`／`flag`付き）
- `delta.py`：差分docの「## 1.」の表を読む（12列固定）。パス定数Dは起点workspaceの絶対パス。別workspaceでは書き換える
- `apply.py 入力.json 出力.json R<番号> 差分doc...`：差分を反映する。監査doc（consistency-audit・value-scope-labels）は監査モードで、分類と※だけを採る。同じ行を精度担当2本が扱うときは、1ファイルずつ順に流す
- `gen3.py master.json body.md`：統合版の§1・§3・§4（`@@MERMAID@@` と `@@STATIC12@@` の置き場あり）と、詳細docのJSON（`body.md.details.json`）を出す
- 組み立て：`head_r15.md`（§0まで）＋body（mermaid_r11.md と static_r13.md を差し込む）＋`tail_r15.md`（§5〜§7）＋`rounds_r15.md`（§8）

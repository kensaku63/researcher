---
name: x-search-plan
description: |
  X 調査の検索戦略を立て、aachat の asks で人間から調査条件をヒアリングし、`aachat session run` で自分自身の新しい `/loop 5m ...` セッションを起動して反復調査を進める skill。
  各ループで新しい検索戦略を立て、`x-search` / `x-search-report` を使ってファクトレポートへ追記し、必要に応じて `x-search-insight` の次論点を次回検索へ戻す。
---

# x-search-plan

X 調査の計画・ヒアリング・反復実行の司令塔になる skill。一次取得は `x-search`、事実整理は `x-search-report`、考察は `x-search-insight` に委譲する。

## いつ呼ぶか

- X 調査を始める前に、検索目的・対象・期間・言語・除外条件を固めたいとき。
- 人間の前提や優先順位を `asks` で確認してから検索したいとき。
- 5 分ごとに検索戦略を更新しながら、ファクトレポートへ追記する自律調査セッションを立ち上げたいとき。
- `x-search-insight` の「次に調べるべき論点」を次の検索条件に落とし込みたいとき。

呼ばないでよいケース:

- 一次取得だけを行う → `x-search`
- 取得済み JSON からファクトレポートを書く → `x-search-report`
- ファクトレポートから考察・仮説を書く → `x-search-insight`

## 成果物

- aachat shared doc 上のヒアリング用 `asks` doc。
- 調査計画: 目的、対象、期間、言語、検索軸、除外条件、事前投稿量確認、初回クエリ群、反証クエリ群。
- 反復調査用の fresh session: `aachat session run <agent> --project <project> "/loop 5m <prompt>"`。
- ループごとに追記されるファクトレポート Markdown。

## 手順

1. **前提を読む**
   - ユーザー依頼、aachat project context、既存 shared docs、既存ファクトレポートを確認する。
   - 既存レポートがある場合は、`status`、検索ログ、失敗した検索、取得できなかった情報、`x-search-insight` の次論点を読む。

2. **ヒアリング要否を判定する**
   - その場の会話だけで決まる軽い確認なら、会話で聞く。
   - 後続の検索戦略に影響する選択肢が 2〜4 個ある場合は、aachat shared doc に `asks` を作る。
   - 認証、secret、`aachat up` など人間操作そのものは `asks` にせず、通常メッセージで依頼する。

3. **asks doc を作る**
   - 場所は `aachat/docs/<team>/<project>/decisions/<id>.md` を原則にする。
   - `asks` は frontmatter の array として書く。
   - 各 option は互いに排他的にし、回答後の検索方針が分かる label にする。
   - 作成後、project に wiki link 1 つを含む短い handoff message を送る。

   ```yaml
   asks:
     - id: search-scope
       question: "今回の X 調査で最優先する範囲はどれですか？"
       options:
         - { value: market-voices, label: "市場の生の声を広く集める" }
         - { value: competitors, label: "競合・代替手段の言及を優先する" }
         - { value: use-cases, label: "利用シーン・困りごとの発見を優先する" }
   ```

4. **検索戦略を確定する**
   - `purpose` は `x-search` / `x-search-report` の enum に合わせる。
   - 1 ターン 1 purpose を守る。混ざる場合は調査を分割する。
   - 調査目的を「観察したい事実」へ分解してから検索語に落とす。仮説や施策案を先に固定しない。
   - 必ず決める項目:
     - `purpose`: `market_research` / `competitor_research` / `trend_discovery` / `influencer_discovery` / `content_planning` / `social_marketing_research`
     - `language`
     - `region`
     - `period`
     - 初回の検索軸 3〜7 個（後述の型から複数選ぶ）
     - 除外語・除外タイプ
     - `counts` で事前確認する候補クエリ
     - 初回 `search` に進める候補クエリ
     - 反証用クエリ候補
     - 成功条件: 何が観察できれば一区切りにするか

5. **初回ファクトレポートを用意する**
   - 既存ファクトレポートに追記する場合は、そのパスを loop prompt に明記する。
   - 新規の場合は `x-search-report` の template / aachat kind に合わせて shared doc を作る。
   - ファクトレポートには、検索ログと代表投稿 ID の安定性を優先する。既存の `Q#N` / `#post-N` / `#group-N` は振り直さない。

6. **自分自身の fresh loop session を起動する**
   - 事前に `aachat project members <project>` で対象 agent 名と live session を確認する。
   - 同じ目的の loop が既にある場合は重複起動しない。続けるなら既存 session に `aachat session send` する。
   - 新しい独立調査なら fresh session を起動する。

   ```bash
   aachat session run <agent> --project <project> "/loop 5m <prompt>"
   ```

7. **loop prompt に含める内容**
   - 調査目的、対象、期間、言語、除外条件。
   - 追記先ファクトレポートのパス。
   - 直近で試した検索ログ、0 件検索、ノイズ過多検索、`queries_built.recommended_excludes[]`、`search_quality`、`next_query_candidates[]` を読むこと。
   - 重複クエリだけでなく、同じ検索軸・同じ語彙・同じ期間の組み合わせを避けること。
   - 各ループで必ず「新しい検索戦略」を 1 つ以上立てること。
   - 主要候補は必要に応じて `x-search counts` で投稿量を確認してから `search` へ進むこと。
   - `x-search` で取得し、`x-search-report` でファクトレポートへ追記すること。
   - 失敗した検索も検索ログに残すこと。
   - 十分な事実が集まったら `x-search-insight` を実行し、次論点を次回 loop に戻すこと。
   - `x-search-insight` の仮説には、次回以降で少なくとも 1 つ反証検索を当てること。
   - 判断が必要な場合だけ `asks` doc を追加し、勝手に大きな前提変更をしないこと。

   prompt 例:

   ```text
   X 調査を継続してください。
   目的: market_research
   対象: <対象>
   期間: 7d
   言語: ja
   追記先ファクトレポート: [[aachat/docs/<team>/<project>/x-search-reports/<id>.md]]

   毎回、既存の検索ログ・失敗した検索・取得できなかった情報・search_quality・next_query_candidates・insight の次論点を読んでから、前回と異なる検索戦略を立ててください。
   主要候補は必要に応じて counts で投稿量を確認し、広すぎる候補は期間短縮・除外語・返信除外で絞り、薄すぎる候補は同義語・代替手段・英語を足してください。
   `x-search` で一次取得し、`x-search-report` のルールでファクトレポートに追記してください。
   代表投稿 ID と検索 ID は既存番号を振り直さず、追記分だけ増やしてください。
   仮説を扱う場合は、支持検索だけでなく反証検索を 1 つ以上試してください。
   調査方針の選択が必要な場合のみ aachat asks doc を作って人間に確認してください。
   ```

## 検索戦略の作り方

- 広い discovery と狭い validation を交互に行う。
- 同義語、ユーザー語彙、競合名、代替手段、課題語、利用シーン語を分けて試す。
- 検索軸は次の型から 3〜7 個を選び、各軸に「観察したい事実」と「期待されるノイズ」を書く。
  - `direct_terms`: 対象を直接表す語彙。例: 製品名、カテゴリ名、略称。
  - `pain_terms`: 困りごと、不満、失敗、面倒、詰まりを表す語彙。
  - `alternative_terms`: 競合、代替手段、手作業、既存ワークフロー。
  - `behavior_terms`: 利用シーン、作業、導入、比較、解約、乗り換えなどの行動語彙。
  - `community_terms`: 職種、界隈、イベント、コミュニティ、属性語彙。
  - `negative_terms`: 仮説を弱める語彙。例: 不要、使わない、問題ない、代替で十分。
- `trend_discovery` / `market_research` / `content_planning` では、主要候補をいきなり `search` せず、可能なら `counts` で投稿量の山と薄さを確認する。
- `counts` の結果が多すぎる場合は、期間短縮、除外語、返信除外、engagement 下限の順で絞る。少なすぎる場合は、同義語、英語、競合・代替手段、期間延長の順で広げる。
- `x-search-insight` から仮説が返っている場合は、次回検索に「支持検索」と「反証検索」を分けて入れる。
- 反証検索は「問題ない」「困っていない」「使わない」「代替で十分」「不要」「やめた」「乗り換えない」など、仮説が崩れる観察を探す語彙を含める。
- 検索済み空間を、クエリ文字列ではなく「検索軸 / 語彙 / 期間 / 除外条件 / 結果状態」で管理する。
- 0 件検索、ノイズ過多検索、取得失敗、認証不足はすべて次回の入力として扱い、同じ失敗を繰り返さない。
- 1 回の loop で目的を増やさない。目的が増えたら別レポートに分ける。
- 0 件クエリは失敗として残し、次回は語彙・期間・除外条件のどれを変えたか明記する。
- 投稿量が多すぎる場合は、期間短縮、除外語、返信除外、エンゲージメント下限の順で絞る。
- 投稿量が少なすぎる場合は、期間延長、同義語追加、英語併用、競合・代替語の追加を試す。

## 検索計画テンプレート

検索計画には、最低限この形を含める。

```markdown
## 調査目的
- purpose:
- 対象:
- 期間 / 言語 / 地域:
- 観察したい事実:
- 成功条件:

## 検索軸
| 軸 ID | 型 | 語彙候補 | 観察したい事実 | 期待されるノイズ | 初回アクション |
|---|---|---|---|---|---|
| A1 | direct_terms | ... | ... | ... | counts -> search |

## 事前投稿量確認
| 候補 | counts 条件 | 広すぎる場合 | 少なすぎる場合 |
|---|---|---|---|
| C1 | ... | ... | ... |

## 初回検索
| クエリ案 | 目的 | 採用条件 | 除外条件 |
|---|---|---|---|
| Q1 | ... | ... | ... |

## 反証検索
| 対象仮説 / 論点 | 反証語彙 | 期待する観察 | 次に使う skill |
|---|---|---|---|
| H1 | ... | ... | x-search |

## 探索済み空間
| 検索軸 | 語彙 | 期間 | 除外条件 | 結果状態 | 次の変更 |
|---|---|---|---|---|---|
| A1 | ... | ... | ... | 0件 / ノイズ過多 / 採用あり | ... |
```

## やらないこと

- `x-search` の代わりにこの skill 内で X API / bird の実行手順を再定義しない。
- ファクトレポートに仮説・施策・コピー案を混ぜない。
- `asks` を作らずに大きな調査目的変更を独断で行わない。
- 同じ project / 同じ目的の `/loop 5m` session を重複起動しない。
- secret、token、raw credential、内部ログを shared doc に書かない。

## 参考

- 隣の `x-search` skill: 構造化検索と一次取得。
- 隣の `x-search-report` skill: ファクトレポートの追記・ID 規約。
- 隣の `x-search-insight` skill: ファクトレポートから次論点を抽出する後続分析。
- aachat `asks` reference: 人間への構造化ヒアリング。

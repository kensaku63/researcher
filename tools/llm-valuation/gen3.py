import json,re,sys
D='/Users/kensakumacstudio/aachat/.run/workspaces/researcher.kensaku63--0486fcc7/aachat/teams/aachat/projects/content-blog/docs/research/'
W='aachat/teams/aachat/projects/content-blog/docs/research/'
m_all=json.load(open(sys.argv[1])); outp=sys.argv[2]
m=[r for r in m_all if not r.get('deleted') and not r.get('appendix')]
app=sorted([r for r in m_all if not r.get('deleted') and r.get('appendix')],key=lambda r:-r['v'])
SEC=[(3,'計算基盤（チップ・クラウド・推論・AIサーバー周辺）','compute'),(1,'基盤モデル開発（ラボ）','labs'),(2,'ビッグテック・大企業のLLM事業','bigtech'),(4,'データ・学習支援・LLMOps・エージェント基盤','data-ops'),(5,'AIコーディング・アプリ開発','coding'),(6,'消費者向け・検索・生産性','consumer'),(7,'業界・業務特化AI','vertical'),(8,'マルチモーダル生成・音声・ロボティクス','multimodal-robotics'),(9,'導入支援・受託（SI）','si')]
TN={t:n for t,n,_ in SEC}
clean=lambda s: re.sub(r'\s+',' ',(s or '')).strip()
def fmt(v): return f'{v:,.0f}' if v>=100 else (f'{v:,.1f}'.rstrip('0').rstrip('.') if v>=1 else f'{v:.2f}'.rstrip('0').rstrip('.'))
m.sort(key=lambda r:-r['v'])
fl=lambda r:'※' if r['flag'] else ''
lab=lambda r: (re.match(r'^［([^］]+)］',r['sub']).group(1) if re.match(r'^［([^］]+)］',r['sub']) else '-')
sub=lambda r: re.sub(r'^［[^］]+］','',r['sub'])
only3=lambda r: '集計③のみ' in r['sub'] or r['sub'].startswith('［境界・③のみ］')
bset=lambda r: (r['type']==3 and '境界' in r['sub']) or only3(r)
t1=[r for r in m if r['type'] in (1,2,4,5,6,7,9) and not only3(r)]
t2=t1+[r for r in m if r['type']==3 and not bset(r)]
t3=m
ex=lambda r: r['sub'].startswith('［全社値・例外］')
tot=sum(r['v'] for r in m)
o=['## 1. LLM事業の種類（9分類の地図）\n','@@MERMAID@@']
o.append('| # | 事業分類 | 件数 | 推定合計（$B） | 構成比 | 主なプレイヤー（推定$B） |\n|---|---|---|---|---|---|')
for i,(t,n,_) in enumerate(SEC,1):
    rs=[r for r in m if r['type']==t]; s=sum(r['v'] for r in rs)
    o.append(f"| {i} | {n} | {len(rs)} | {s:,.0f} | {s/tot:.1%} | "+'、'.join(f"{re.split('[（：]',r['name'])[0]} {fmt(r['v'])}" for r in rs[:6])+' |')
o.append(f"| | 合計 | {len(m)} | 約{tot:,.0f} | | |\n")
o.append('合計は、どこまでを「LLM事業」と数えるかで3通りに示す。機能の分類（1〜9）と、どの合計に入れるか（①②③）は別の軸。合計は丸める前の点推定から計算しているので、表示値の単純な和とは一致しないことがある。\n')
o.append('| 数え方 | 含めるもの | 件数 | 推定合計（$B） | 厳格版：件数 | 厳格版：合計（$B） |\n|---|---|---|---|---|---|')
for labl,desc,a in [('① LLMソフトウェア・サービス','基盤モデル、ビッグテックのLLM事業、データ・LLMOps、コーディング、消費者向け、業界・業務特化、導入支援。「③のみ」の行は除く',t1),('② ①＋LLM向け計算基盤','①に、チップ・メモリ・GPUクラウド・推論APIを足す',t2),('③ ②＋境界例','②に、マルチモーダル・ロボティクスと、AIサーバー・電源・光通信・DCなどを足す（全件）',t3)]:
    b=[r for r in a if not ex(r)]
    o.append(f"| {labl} | {desc} | {len(a)} | 約{sum(r['v'] for r in a):,.0f} | {len(b)} | 約{sum(r['v'] for r in b):,.0f} |")
o.append('\n厳格版は、会社全体の値で載せていて、本業がAI以前からある事業の行（ラベルが［全社値・例外］の行）を外した数え方。§4.10の参考表と同じ物差しにそろえた合計になる。\n')
o.append('@@STATIC12@@\n')
o.append('## 3. 推定時価総額ランキング 上位50\n')
o.append('| 順位 | 企業・事業 | 国 | 事業分類 | 推定（$B） | レンジ（$B） |\n|---|---|---|---|---|---|')
for i,r in enumerate(m[:50],1):
    o.append(f"| {i} | {clean(r['name'])}{fl(r)} | {r['country']} | {TN[r['type']]} | {fmt(r['v'])} | {clean(r['range']) or '-'} |")
o.append('\n## 4. 全リスト（事業分類ごと）\n')
o.append('各分類の中で、推定値の大きい順に並べた。「推定」は2026-10時点の事業価値で、単位は$B（10億ドル）。\n\n- 社名に※が付いた行は、2026年の値を一次情報や信頼できる報道でまだ確認できていない。\n- 「値の範囲」は、その行の値が何を測っているかを示す（§2）。\n- 事業の説明、直近の公表値、推定根拠、出典、調査の経緯は、分類ごとの詳細docにある。\n')
details={}
for i,(t,n,slug) in enumerate(SEC,1):
    rs=[r for r in m if r['type']==t]
    link=f"{W}llm-business-valuation-20261026-detail-{i}-{slug}.md"
    o.append(f"### 4.{i} {n}（{len(rs)}件、合計 約${sum(r['v'] for r in rs):,.0f}B）\n")
    o.append(f"詳細：[[{link}]]\n")
    o.append('| # | 企業・事業 | 国 | 細分類 | 値の範囲 | 推定（$B） | レンジ |\n|---|---|---|---|---|---|---|')
    for j,r in enumerate(rs,1):
        o.append(f"| {j} | {clean(r['name'])}{fl(r)} | {r['country']} | {clean(sub(r))} | {lab(r)} | {fmt(r['v'])} | {clean(r['range']) or '-'} |")
    o.append('')
    dd=[f'''---
title: "LLM事業の地図と推定時価総額 詳細{i}：{n}"
summary: 統合版§4.{i}の詳細。{n}の{len(rs)}件について、事業の説明・直近の公表値・推定値とレンジ・推定根拠・出典・調査の経緯を並べた。
status: final
tags: [research, llm-business, valuation, 2026-10-26]
related:
  - aachat/teams/aachat/projects/content-blog/docs/research/llm-business-valuation-20261026.md
---

# 詳細{i}：{n}（{len(rs)}件、合計 約${sum(r['v'] for r in rs):,.0f}B）

> 統合版 [[aachat/teams/aachat/projects/content-blog/docs/research/llm-business-valuation-20261026.md]] の§4.{i}の詳細。定義と推定の方法は統合版の§2にある。「調査」列は、その行の値を出した調査を示す。「担当N」は初回の担当ノート、「R1-A」などはラウンドごとの差分docで、矢印は更新の順。

| # | 企業・事業 | 国 | 細分類 | 何をしているか | 直近の公表値 | 推定（$B） | レンジ | 推定根拠 | 出典 | 調査 |
|---|---|---|---|---|---|---|---|---|---|---|''']
    for j,r in enumerate(rs,1):
        dd.append(f"| {j} | {clean(r['name'])}{fl(r)} | {r['country']} | {clean(r['sub'])} | {clean(r['what'])} | {clean(r['pub'])} | {fmt(r['v'])} | {clean(r['range']) or '-'} | {clean(r['basis'])} | {clean(r.get('src','')) or '担当ノート'} | {r['prov']} |")
    details[f"llm-business-valuation-20261026-detail-{i}-{slug}.md"]='\n'.join(dd)+'\n'
o.append(f"### 4.10 参考：既存事業へのAI追加・その他の境界企業（{len(app)}件、合計外）\n")
o.append(f"既存の事業にAI機能を足した会社や、本表の対象に入れなかった境界の会社。会社全体の参考評価額なので、§1の合計には入れていない。表は別の文書にある：[[{W}llm-business-valuation-20261026-appendix.md]]\n")
ap=[f'''---
title: "LLM事業の地図と推定時価総額 参考表：既存事業へのAI追加・その他の境界企業"
summary: 統合版の本表に入れなかった{len(app)}件の参考表。既存の事業にAI機能を足した会社や、本表の対象に入れなかった境界の会社を、会社全体の参考評価額とともに並べた。合計には入れない。
status: final
tags: [research, llm-business, valuation, 2026-10-26]
related:
  - aachat/teams/aachat/projects/content-blog/docs/research/llm-business-valuation-20261026.md
---

# 参考：既存事業へのAI追加・その他の境界企業（{len(app)}件、合計外）

> 統合版 [[aachat/teams/aachat/projects/content-blog/docs/research/llm-business-valuation-20261026.md]] の§4.10。ラウンド10-C〜12-Cで、ユニコーンの全件リスト（CB Insights・Hurun・Crunchbase、計約5,000行）と突き合わせて見つかった会社が中心。既存の事業にAI機能を足した会社や、本表の対象に入れなかった境界の会社で、ソフトウェアだけでなく金融・端末・製造・防衛も含む。値は会社全体の評価額で、多くはユニコーン一覧の掲載値（※）。LLM部分の値ではないので、統合版の合計には入れていない。

| # | 企業 | 国 | 分類 | 細分類 | LLM・AIエージェントの機能 | 直近の公表値 | 全社の評価額（$B） | 出典 |
|---|---|---|---|---|---|---|---|---|''']
for j,r in enumerate(app,1):
    ap.append(f"| {j} | {clean(r['name'])}{fl(r)} | {r['country']} | {TN[r['type']]} | {clean(r['sub'])} | {clean(r['what'])} | {clean(r['pub'])} | {fmt(r['v'])} | {clean(r.get('src','')) or '-'} |")
details['llm-business-valuation-20261026-appendix.md']='\n'.join(ap)+'\n'
open(outp,'w').write('\n'.join(o))
json.dump(details,open(outp+'.details.json','w'),ensure_ascii=False)
print(len(m),round(tot),sum(1 for r in m if r['flag']),{k:len(v.encode()) for k,v in details.items()})

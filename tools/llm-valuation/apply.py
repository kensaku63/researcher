import json,re,sys
sys.path.insert(0,'/tmp/llm-val')
from delta import parse,norm,SEC2CODE
master=json.load(open(sys.argv[1])); out=sys.argv[2]; tag=sys.argv[3]  # e.g. R1
ALIAS={'cipherdigital':'ciphermining'}
def num(s):
    m=re.match(r'\s*([\d,]+(?:\.\d+)?)',s); return float(m.group(1).replace(',','')) if m else None
files=sys.argv[4:]
cuts=[]
log={'new':0,'upd':0,'conf':0,'del':0,'changed_value':0}
done=set()
for f in files:
    lab=tag+'-'+re.search(r'-r\d+-([a-z])-',f).group(1).upper()
    audit=('consistency-audit' in f) or ('value-scope-labels' in f) or lab.endswith('C') and 'coverage' not in f
    for d in parse(f):
        idx={norm(r['name']):i for i,r in enumerate(master)}
        k=norm(d['name']); k=k if k in idx else ALIAS.get(k,k)
        v=num(d['est'])
        try: cat=SEC2CODE[int(re.match(r'\d',d['cat']).group(0))]
        except: cat=None
        if d['basis'].startswith('範囲統一の試算'):
            cuts.append(dict(name=d['name'],old=d['old'],est=d['est'],range=d['range'],basis=d['basis'])); continue
        if d['name'].startswith('Hugging Face（統合版'):
            d['kind']='新規'; d['name']='Hugging Face（Nvidiaが買収合意、未完了）'
        if d['kind']=='新規':
            master.append(dict(part=lab,name=d['name'].replace('※',''),country=d['country'],sub=d['sub'],what=d['what'],pub=d['pub'],rev='',est=d['est'],v=v,basis=d['basis'],type=cat,range=d['range'],flag='※' in d['name'],src=d['src'],prov=lab,appendix=d['sub'].startswith('参考：AI機能を足した既存事業'))); log['new']+=1; continue
        i=idx.get(k)
        if i is None:
            if d['kind']!='確認のみ': print('UNMATCHED',lab,d['name'])
            continue
        r=master[i]
        if k in done and lab.endswith('B') and not audit: # A already updated this row: keep A values
            continue
        if k in done and audit:
            if cat and cat!=r['type']: r['type']=cat
            r['basis']=r['basis']+' ［整合性の点検］'+d['basis'][:200]
            continue
        if d['kind']=='削除':
            r['deleted']=lab+'：'+d['basis']; log['del']+=1; continue
        if v is not None and r['v'] is not None and abs(v-r['v'])>1e-9: log['changed_value']+=1
        if d['kind']=='確認のみ': log['conf']+=1
        else: log['upd']+=1
        nsub=d['sub'] or r['sub']
        lm=re.match(r'^(［[^］]+］)',r['sub'] or '')
        if lm and not nsub.startswith('［'): nsub=lm.group(1)+nsub
        r.update(name=d['name'].replace('※',''),country=d['country'] or r['country'],sub=nsub,what=d['what'] or r['what'],pub=d['pub'],est=d['est'],range=d['range'],basis=d['basis'],src=d['src'],prov=r['prov']+'→'+lab)
        if v is not None: r['v']=v
        if cat and cat!=r['type'] and (audit or lab.endswith('C')): print('TYPE CHANGE',r['name'],r['type'],'->',cat); r['type']=cat
        if audit or (lab.endswith('C')): r['flag']=r['flag'] or ('※' in d['name'])
        else: r['flag']=('※' in d['name'])
        if lab.endswith('A'): done.add(norm(r['name']))
json.dump(master,open(out,'w'),ensure_ascii=False)
if cuts: json.dump(cuts,open(out.replace('.json','_cuts.json'),'w'),ensure_ascii=False)
live=[r for r in master if not r.get('deleted')]
print(log, 'rows',len(live),'total',round(sum(r['v'] or 0 for r in live),1),'flags',sum(1 for r in live if r['flag']))

import json,re,sys
D='/Users/kensakumacstudio/aachat/.run/workspaces/researcher.kensaku63--0486fcc7/aachat/teams/aachat/projects/content-blog/docs/research/'
SEC2CODE={1:3,2:1,3:2,4:4,5:5,6:6,7:7,8:8,9:9}
def norm(n): 
    n=n.replace('※','')
    n=re.split(r'[（(：:]',n)[0]
    return re.sub(r'[\s・/／]','',n).lower()
def parse(f):
    txt=open(D+f).read()
    m=re.search(r'^## 1\..*?(?=^## 2\.)',txt,re.S|re.M)
    out=[]
    for line in m.group(0).splitlines():
        if not re.match(r'^\| (新規|更新|削除|確認のみ) ',line): continue
        c=[x.strip() for x in line.strip().strip('|').split('|')]
        if len(c)!=12: print('BADCOLS',f,len(c),c[:2],file=sys.stderr); continue
        out.append(dict(kind=c[0],name=c[1],country=c[2],cat=c[3],sub=c[4],what=c[5],pub=c[6],est=c[7],range=c[8],basis=c[9],src=c[10],old=c[11]))
    return out
if __name__=='__main__':
    master=json.load(open(sys.argv[1]))
    idx={}
    for i,r in enumerate(master): idx.setdefault(norm(r['name']),i)
    for f in sys.argv[2:]:
        rows=parse(f); um=[]
        for d in rows:
            if d['kind']=='新規':
                if norm(d['name']) in idx: um.append(('NEW-but-exists',d['name']))
                continue
            k=norm(d['name'])
            if k not in idx:
                cand=[n for n in idx if n.startswith(k) or k.startswith(n)]
                um.append((d['kind'],d['name'],cand[:3]))
        print(f,len(rows),'unmatched:',um)

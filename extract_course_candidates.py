#!/usr/bin/env python3
"""Extract PAR candidates from approved course tables. NEVER publish them automatically."""
import csv, json, re, sys, html
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
FIELDS=['골프장명','지역','코스명']+[f'PAR{i}' for i in range(1,10)]+['출처URL','확인일']

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0;self.row=None;self.cell=None;self.table=None;self.tables=[]
    def handle_starttag(self,tag,attrs):
        if tag=='table':
            if self.depth==0:self.table=[]
            self.depth+=1
        elif self.depth and tag=='tr':self.row=[]
        elif self.depth and tag in ('td','th'):self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(' '.join(''.join(self.cell).split()))
            self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.table is not None:self.table.append(self.row)
            self.row=None
        elif tag=='table' and self.depth:
            self.depth-=1
            if self.depth==0 and self.table is not None:self.tables.append(self.table);self.table=None

def pars_from_table(rows):
    # Only accept an explicit 1-9 hole header AND an explicit PAR row with 9 values.
    header=any([re.sub(r'\D','',c)==str(i) for i,c in enumerate(r[1:10],1)]==[True]*9 for r in rows if len(r)>=10)
    if not header:return None
    options=[]
    for row in rows:
        if len(row)<10 or not re.fullmatch(r'(?i)par\s*',row[0]):continue
        vals=row[1:10]
        if any(not re.fullmatch(r'[3-6]',v) for v in vals):continue
        pars=[int(v) for v in vals]
        if len(row)>=11 and row[10].isdigit() and int(row[10])!=sum(pars):continue
        options.append(pars)
    return options[0] if len(options)==1 else None

def fetch(url):
    if urlparse(url).scheme!='https':raise ValueError('HTTPS only')
    req=Request(url,headers={'User-Agent':'OJANG-Golf-Course-Candidate-Review/1.0','Accept':'text/html'})
    with urlopen(req,timeout=25) as resp:
        data=resp.read(3_000_001)
        if len(data)>3_000_000:raise ValueError('page too large')
        encoding=resp.headers.get_content_charset() or 'utf-8'
    return data.decode(encoding,errors='replace')

def main():
    with (ROOT/'verified_courses.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    config=json.loads((ROOT/'course_sources.json').read_text(encoding='utf-8'))
    bykey={(r['골프장명'],r['코스명']):r for r in rows}
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    output=[];status=[]
    for item in config['sources']:
        club=item['club'];url=item['url'];courses=item['courses'];region=item['region']
        try:
            doc=Tables();doc.feed(fetch(url))
            extracted=[p for t in doc.tables if (p:=pars_from_table(t)) is not None]
            if len(extracted)!=len(courses):
                status.append(f'{club}: 보류 (코스표 {len(extracted)}개 발견 / 설정 {len(courses)}개, 페이지 구조 검토 필요)')
                continue
            for course,pars in zip(courses,extracted):
                old=bykey.get((club,course))
                oldpars=[int(old[f'PAR{i}']) for i in range(1,10)] if old else None
                decision='일치' if oldpars==pars else ('신규 후보' if oldpars is None else '변경 후보')
                output.append({'골프장명':club,'지역':region,'코스명':course,**{f'PAR{i}':pars[i-1] for i in range(1,10)},'출처URL':url,'확인일':now[:10],'상태':decision})
                status.append(f'{club}/{course}: {decision} (PAR {sum(pars)})')
        except Exception as e:
            status.append(f'{club}: 접속/추출 실패 ({type(e).__name__})')
    with (ROOT/'course_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS+['상태']);w.writeheader();w.writerows(output)
    summary='\n'.join(['# 공식 코스 PAR 자동 추출 후보',f'실행: {now}','',*['- '+s for s in status],'','**안전장치:** 후보 파일은 자동 게시되지 않습니다. 공식 자료와 대조한 뒤 검증 CSV에 수동 반영해야 합니다.'])+'\n'
    print(summary)
    import os
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(summary)
    return 0
if __name__=='__main__':sys.exit(main())

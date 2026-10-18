#!/usr/bin/env python3
"""Extract review-only PAR candidates from approved official pages. Never auto-publish."""
import csv, json, re, os, sys
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
        self.stack=[];self.tables=[];self.visible=[];self.ignore=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.ignore+=1
        if tag=='table':self.stack.append({'rows':[],'row':None,'cell':None})
        elif self.stack and tag=='tr':self.stack[-1]['row']=[]
        elif self.stack and tag in ('td','th'):self.stack[-1]['cell']=[]
    def handle_data(self,data):
        if not self.ignore and data.strip():self.visible.append(data.strip())
        if self.stack and self.stack[-1]['cell'] is not None:self.stack[-1]['cell'].append(data)
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.ignore=max(0,self.ignore-1)
        if not self.stack:return
        t=self.stack[-1]
        if tag in ('td','th') and t['cell'] is not None:
            if t['row'] is not None:t['row'].append(' '.join(''.join(t['cell']).split()))
            t['cell']=None
        elif tag=='tr' and t['row'] is not None:
            t['rows'].append(t['row']);t['row']=None
        elif tag=='table':
            self.tables.append(self.stack.pop()['rows'])

def parse_par_table(rows):
    header=any(len(r)>=10 and all(re.sub(r'\D','',r[i])==str(i) for i in range(1,10)) for r in rows)
    if not header:return None
    candidates=[]
    for r in rows:
        if len(r)<10 or not re.fullmatch(r'par\s*',r[0],re.I):continue
        if any(not re.fullmatch('[3-6]',v) for v in r[1:10]):continue
        vals=[int(v) for v in r[1:10]]
        if len(r)>10 and re.fullmatch(r'\d+',r[10]) and int(r[10])!=sum(vals):continue
        candidates.append(vals)
    return candidates[0] if len(candidates)==1 else None

def fallback_pars(visible):
    """Review-only fallback for sites using div grids or nested/irregular HTML tables."""
    tokens=[]
    for t in visible:
        tokens.extend(re.findall(r'(?i)\b(?:hole|par|total)\b|\d+',t))
    results=[]
    for i,t in enumerate(tokens):
        if t.lower()!='hole' or tokens[i+1:i+10]!=[str(n) for n in range(1,10)]:continue
        stop=next((j for j in range(i+10,min(i+100,len(tokens))) if tokens[j].lower()=='hole'),min(i+100,len(tokens)))
        for j in range(i+10,stop):
            if tokens[j].lower()!='par':continue
            p=tokens[j+1:j+10]
            if len(p)!=9 or any(x not in ('3','4','5','6') for x in p):continue
            vals=list(map(int,p))
            if j+10<len(tokens) and tokens[j+10].isdigit() and int(tokens[j+10])!=sum(vals):continue
            results.append(vals);break
    return results

def named_pars_from_visible(visible, courses):
    """Associate each PAR block with an explicit course name, not page order.

    Supports course labels like Gold(Membership) followed by Hole 1..9 and Par.
    If the page cannot be unambiguously mapped, return None.
    """
    text=' '.join(visible)
    # Match English course labels commonly shown by the official club.
    aliases={'골드':r'Gold', '레이크':r'Lake', '힐':r'Hill', '밸리':r'Valley'}
    patterns=[]
    for course in courses:
        label=aliases.get(course, re.escape(course))
        matches=list(re.finditer(r'(?i)(?<![A-Za-z])'+label+r'(?![A-Za-z])(?:\s*\([^)]{0,60}\))?',text))
        patterns.append((course,matches))
    # Scan each named block and require exactly one unambiguous sequence.
    found={}
    for course,matches in patterns:
        options=set()
        for m in matches:
            tail=text[m.end():m.end()+2400]
            # Must have an explicit hole header before PAR; avoid unrelated numbers.
            hole=re.search(r'(?i)\bHole\b\s*1\s+2\s+3\s+4\s+5\s+6\s+7\s+8\s+9\b',tail)
            if not hole:continue
            section=tail[hole.end():]
            par=re.search(r'(?i)\bPar\b\s+([3-6](?:\s+[3-6]){8})(?!\s*[3-6])',section)
            if not par:continue
            # Do not associate PAR beyond the next named course label.
            intervening=tail[:hole.end()+par.start()]
            other_labels=[aliases.get(c,re.escape(c)) for c in courses if c!=course]
            if other_labels and re.search(r'(?i)\b(?:'+'|'.join(other_labels)+r')\s*\((?:Membership|Public)\)',intervening):continue
            vals=tuple(map(int,par.group(1).split()))
            after=section[par.end():par.end()+10]
            total=re.match(r'\s+(\d{2})\b',after)
            if total and int(total.group(1))!=sum(vals):continue
            options.add(vals)
        if len(options)!=1:return None
        found[course]=list(next(iter(options)))
    return found

def diagnose(visible, tables):
    """Bounded, non-sensitive text diagnostics for GitHub Actions summary."""
    text=' '.join(visible)
    samples=[]
    for term in ('Gold(Membership)','Lake(Membership)','Hill(Public)','Valley(Public)','Hole','Par'):
        m=re.search(re.escape(term),text,re.I)
        if m:
            snippet=' '.join(text[max(0,m.start()-35):m.start()+200].split())
            samples.append(term+': '+snippet[:220])
    return f'HTML 표 {len(tables)}개 / 텍스트 토큰 {len(visible)}개; 예시: '+(' | '.join(samples[:4]) if samples else '키워드 미발견')

def fetch(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.netloc:raise ValueError('HTTPS only')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; OJANG-Golf-Review/2.0)','Accept':'text/html'})
    with urlopen(req,timeout=25) as resp:
        data=resp.read(3_000_001)
        if len(data)>3_000_000:raise ValueError('Page too large')
        charset=resp.headers.get_content_charset()
    if charset:return data.decode(charset,errors='replace')
    for enc in ('utf-8','cp949'):
        try:return data.decode(enc)
        except UnicodeDecodeError:pass
    return data.decode('utf-8',errors='replace')

def main():
    with (ROOT/'verified_courses.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    config=json.loads((ROOT/'course_sources.json').read_text(encoding='utf-8'))
    known={(r['골프장명'],r['코스명']):r for r in rows}
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out=[];status=[]
    for item in config['sources']:
        club=item['club'];url=item['url'];courses=item['courses'];region=item['region']
        try:
            doc=Tables();doc.feed(fetch(url))
            # Prefer explicit course-name association; never assume table order.
            named=named_pars_from_visible(doc.visible,courses)
            if named is not None:
                extracted=[named[c] for c in courses]
                method='코스명 연결 분석'
            else:
                extracted=[p for t in doc.tables if (p:=parse_par_table(t)) is not None]
                method='표 순서 (기존 등록 PAR 교차검증 필수)'
                if len(extracted)!=len(courses):
                    fallback=fallback_pars(doc.visible)
                    if len(fallback)==len(courses):extracted=fallback;method='텍스트 순서 (기존 등록 PAR 교차검증 필수)'
                if len(extracted)==len(courses):
                    # Prevent silent mis-association: only use order-based extraction
                    # if every course already exists and its PAR matches exactly.
                    matched=True
                    for course,pars in zip(courses,extracted):
                        old=known.get((club,course))
                        if old is None or [int(old[f'PAR{i}']) for i in range(1,10)]!=pars:
                            matched=False;break
                    if not matched:extracted=[]
            if len(extracted)!=len(courses):
                status.append(f'{club}: 보류 (확정 추출 {len(extracted)}개 / 설정 {len(courses)}개)')
                status.append(f'{club} 진단: {diagnose(doc.visible,doc.tables)}')
                continue
            for course,pars in zip(courses,extracted):
                old=known.get((club,course))
                oldpars=[int(old[f'PAR{i}']) for i in range(1,10)] if old else None
                decision='일치' if oldpars==pars else ('신규 후보' if oldpars is None else '변경 후보')
                out.append({'골프장명':club,'지역':region,'코스명':course,**{f'PAR{i}':pars[i-1] for i in range(1,10)},'출처URL':url,'확인일':now[:10],'상태':decision})
                status.append(f'{club}/{course}: {decision} (PAR {sum(pars)}, {method})')
        except Exception as e:status.append(f'{club}: 접속/추출 실패 ({type(e).__name__})')
    with (ROOT/'course_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS+['상태']);w.writeheader();w.writerows(out)
    summary='\n'.join(['# 공식 코스 PAR 자동 추출 후보',f'실행: {now}','',*['- '+s for s in status],'','**안전장치:** 추출 후보는 자동 게시되지 않습니다.'])+'\n'
    print(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(summary)
    return 0
if __name__=='__main__':sys.exit(main())


def parse_par_table(rows):
    header=any(len(r)>=10 and all(re.sub(r'\D','',r[i])==str(i) for i in range(1,10)) for r in rows)
    if not header:return None
    candidates=[]
    for r in rows:
        if len(r)<10 or not re.fullmatch(r'par\s*',r[0],re.I):continue
        if any(not re.fullmatch('[3-6]',v) for v in r[1:10]):continue
        vals=[int(v) for v in r[1:10]]
        if len(r)>10 and re.fullmatch(r'\d+',r[10]) and int(r[10])!=sum(vals):continue
        candidates.append(vals)
    return candidates[0] if len(candidates)==1 else None

def fallback_pars(visible):
    """Review-only fallback for sites using div grids or nested/irregular HTML tables."""
    tokens=[]
    for t in visible:
        tokens.extend(re.findall(r'(?i)\b(?:hole|par|total)\b|\d+',t))
    results=[]
    for i,t in enumerate(tokens):
        if t.lower()!='hole' or tokens[i+1:i+10]!=[str(n) for n in range(1,10)]:continue
        stop=next((j for j in range(i+10,min(i+100,len(tokens))) if tokens[j].lower()=='hole'),min(i+100,len(tokens)))
        for j in range(i+10,stop):
            if tokens[j].lower()!='par':continue
            p=tokens[j+1:j+10]
            if len(p)!=9 or any(x not in ('3','4','5','6') for x in p):continue
            vals=list(map(int,p))
            if j+10<len(tokens) and tokens[j+10].isdigit() and int(tokens[j+10])!=sum(vals):continue
            results.append(vals);break
    return results

def fetch(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.netloc:raise ValueError('HTTPS only')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; OJANG-Golf-Review/2.0)','Accept':'text/html'})
    with urlopen(req,timeout=25) as resp:
        data=resp.read(3_000_001)
        if len(data)>3_000_000:raise ValueError('Page too large')
        charset=resp.headers.get_content_charset()
    if charset:return data.decode(charset,errors='replace')
    for enc in ('utf-8','cp949'):
        try:return data.decode(enc)
        except UnicodeDecodeError:pass
    return data.decode('utf-8',errors='replace')

def main():
    with (ROOT/'verified_courses.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    config=json.loads((ROOT/'course_sources.json').read_text(encoding='utf-8'))
    known={(r['골프장명'],r['코스명']):r for r in rows}
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out=[];status=[]
    for item in config['sources']:
        club=item['club'];url=item['url'];courses=item['courses'];region=item['region']
        try:
            doc=Tables();doc.feed(fetch(url))
            extracted=[p for t in doc.tables if (p:=parse_par_table(t)) is not None]
            method='표'
            if len(extracted)!=len(courses):
                fallback=fallback_pars(doc.visible)
                if len(fallback)==len(courses):extracted=fallback;method='텍스트 보조 분석'
            if len(extracted)!=len(courses):
                status.append(f'{club}: 보류 (코스표 {len(extracted)}개 발견 / 설정 {len(courses)}개, 페이지 구조 검토 필요)');continue
            for course,pars in zip(courses,extracted):
                old=known.get((club,course))
                oldpars=[int(old[f'PAR{i}']) for i in range(1,10)] if old else None
                decision='일치' if oldpars==pars else ('신규 후보' if oldpars is None else '변경 후보')
                out.append({'골프장명':club,'지역':region,'코스명':course,**{f'PAR{i}':pars[i-1] for i in range(1,10)},'출처URL':url,'확인일':now[:10],'상태':decision})
                status.append(f'{club}/{course}: {decision} (PAR {sum(pars)}, {method})')
        except Exception as e:status.append(f'{club}: 접속/추출 실패 ({type(e).__name__})')
    with (ROOT/'course_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS+['상태']);w.writeheader();w.writerows(out)
    summary='\n'.join(['# 공식 코스 PAR 자동 추출 후보',f'실행: {now}','',*['- '+s for s in status],'','**안전장치:** 추출 후보는 자동 게시되지 않습니다.'])+'\n'
    print(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(summary)
    return 0
if __name__=='__main__':sys.exit(main())

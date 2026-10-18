#!/usr/bin/env python3
"""Extract review-only PAR candidates from approved official pages. Never auto-publish."""
import csv, json, re, os, sys
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlparse, urlunparse
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
            par=re.search(r'(?i)\bPar\b\s+([3-6](?:\s+[3-6]){8})(?=\s|$)',section)
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
    """Return HTML plus limited response metadata for troubleshooting."""
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in ('www.goldlake.co.kr','goldlake.co.kr','www.kjcc.co.kr'):
        raise ValueError('Unapproved HTTPS source')
    req=Request(url,headers={
        'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
        'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language':'ko-KR,ko;q=0.9,en-US;q=0.7',
    })
    with urlopen(req,timeout=25) as resp:
        final=resp.geturl()
        if urlparse(final).hostname not in ('www.goldlake.co.kr','goldlake.co.kr','www.kjcc.co.kr'):
            raise ValueError('Redirected outside approved domain')
        data=resp.read(3_000_001)
        if len(data)>3_000_000:raise ValueError('Page too large')
        charset=resp.headers.get_content_charset()
        code=resp.status
        ctype=resp.headers.get('Content-Type','')
    if charset:
        page=data.decode(charset,errors='replace')
    else:
        page=None
        for enc in ('utf-8','cp949'):
            try:page=data.decode(enc);break
            except UnicodeDecodeError:pass
        if page is None:page=data.decode('utf-8',errors='replace')
    return page,{'code':code,'final':final,'bytes':len(data),'type':ctype}

def variants(url):
    """Probe approved official Goldlake course-page routes, never mirrors.

    The legacy .asp path can redirect to /index on GitHub runners. The
    extensionless /course/course01 is independently indexed as an official
    course page, so try that path too. This is diagnostic, not a bypass.
    """
    parsed=urlparse(url)
    if parsed.hostname not in ('www.goldlake.co.kr','goldlake.co.kr'):
        return [url]
    hosts=('www.goldlake.co.kr','goldlake.co.kr')
    paths=('/course/course01','/course/course01.asp')
    result=[]
    for host in hosts:
        for path in paths:
            candidate=urlunparse(parsed._replace(netloc=host,path=path,params='',query='',fragment=''))
            if candidate not in result:result.append(candidate)
    return result

def response_diagnostic(doc, meta):
    text=' '.join(doc.visible)
    title=re.search(r'<title[^>]*>(.*?)</title\s*>',doc.raw,re.I|re.S)
    title=' '.join(re.sub(r'<[^>]*>','',title.group(1)).split())[:90] if title else '(없음)'
    preview=' '.join(text.split())[:110] or '(표시 텍스트 없음)'
    markers=','.join(k for k in ('Gold','Lake','Hill','Valley','Hole','Par') if re.search(r'(?i)\b'+k+r'\b',text)) or '없음'
    return f"HTTP {meta['code']} / {meta['bytes']}바이트 / 표 {len(doc.tables)}개 / 코스표 키워드 {markers} / 제목 {title!r} / 첫 문장 {preview!r} / 최종주소 {meta['final']}"

def extract_page(doc,courses,known,club):
    """Require named course blocks, or exact-match verified order fallback."""
    named=named_pars_from_visible(doc.visible,courses)
    if named is not None:
        return [named[c] for c in courses],'코스명 연결 분석'
    extracted=[p for t in doc.tables if (p:=parse_par_table(t)) is not None]
    method='표 순서 (기존 등록 PAR 교차검증)'
    if len(extracted)!=len(courses):
        fallback=fallback_pars(doc.visible)
        if len(fallback)==len(courses):
            extracted=fallback;method='텍스트 순서 (기존 등록 PAR 교차검증)'
    if len(extracted)==len(courses):
        for course,pars in zip(courses,extracted):
            old=known.get((club,course))
            if old is None or [int(old[f'PAR{i}']) for i in range(1,10)]!=pars:
                return [],'불확실한 코스 대응으로 보류'
    return extracted,method

def main():
    with (ROOT/'verified_courses.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    config=json.loads((ROOT/'course_sources.json').read_text(encoding='utf-8'))
    known={(r['골프장명'],r['코스명']):r for r in rows}
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out=[];status=[]
    for item in config['sources']:
        club=item['club'];url=item['url'];courses=item['courses'];region=item['region']
        try:
            extracted=[];method='';diagnostics=[]
            for candidate_url in variants(url):
                try:
                    page,meta=fetch(candidate_url)
                    doc=Tables();doc.raw=page;doc.feed(page)
                    diagnostics.append(candidate_url+' => '+response_diagnostic(doc,meta))
                    final_path=urlparse(meta['final']).path.rstrip('/')
                    if club=='골드레이크CC' and final_path not in ('/course/course01','/course/course01.asp'):
                        diagnostics[-1]+=' [보류: 코스제원 이외 주소로 이동]'
                        continue
                    found,found_method=extract_page(doc,courses,known,club)
                    if len(found)==len(courses):
                        extracted=found;method=found_method
                        # A different hostname is still the same official club domain.
                        if candidate_url!=url:method+=' (공식 대체 호스트)'
                        break
                except Exception as variant_error:
                    diagnostics.append(candidate_url+': '+type(variant_error).__name__)
            if len(extracted)!=len(courses):
                status.append(f'{club}: 보류 (확정 추출 {len(extracted)}개 / 설정 {len(courses)}개)')
                status.extend(f'{club} 응답 {n}: {info}' for n,info in enumerate(diagnostics,1))
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

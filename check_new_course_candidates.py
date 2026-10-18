#!/usr/bin/env python3
"""Review-only official page checker. Never changes catalog or verified_courses.csv."""
import csv
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'new_course_candidates.csv'
OUTPUT = ROOT / 'new_course_review.csv'
FIELDS = ['골프장명','지역','코스명'] + [f'PAR{i}' for i in range(1,10)] + ['출처URL','확인일','상태','검사메모']

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
        self.ignored=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'): self.ignored+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.ignored=max(0,self.ignored-1)
    def handle_data(self,data):
        if not self.ignored and data.strip(): self.parts.append(' '.join(data.split()))

def fetch(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('www.beachegolf.com','beachegolf.com'):
        raise ValueError('허용되지 않은 공식 주소')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; OjangGolfReview/1.0)','Accept-Language':'ko-KR,ko;q=0.9'})
    with urlopen(req,timeout=25) as response:
        final=response.geturl()
        if urlparse(final).hostname not in ('www.beachegolf.com','beachegolf.com'):
            raise ValueError('다른 도메인으로 이동')
        data=response.read(2_000_001)
        if len(data)>2_000_000: raise ValueError('응답 용량 초과')
        enc=response.headers.get_content_charset()
    if enc:
        page=data.decode(enc,errors='replace')
    else:
        for codec in ('utf-8','cp949','euc-kr'):
            try: page=data.decode(codec);break
            except UnicodeDecodeError: continue
        else: page=data.decode('utf-8',errors='replace')
    doc=TextParser();doc.feed(page)
    return final,doc.parts

def inspect(parts,course,pars):
    # Name-bound review: each named section must precede the next named section.
    text=' '.join(parts)
    names=['다산','베아채','장보고']
    matches={name:list(re.finditer(re.escape(name)+r'\s*코스',text)) for name in names}
    if not matches[course]: return '보류','코스 이름을 찾지 못함'
    candidates=[]
    for m in matches[course]:
        end=min((x.start() for name in names if name!=course for x in matches[name] if x.start()>m.start()),default=len(text))
        segment=text[m.end():end]
        # Prefer explicit PAR label and exact nine 3..6 values plus 36 total.
        pattern=r'(?i)(?:\bPAR\b)\s*'+r'\s+'.join(str(n) for n in pars)+r'\s+'+str(sum(pars))+r'(?!\d)'
        if re.search(pattern,segment): candidates.append(True)
    if candidates:return '후보 일치(검토 필요)','코스명 뒤 PAR 9개와 합계가 텍스트에서 확인됨; 표 구조 수동 확인 권장'
    return '보류','코스별 PAR 행을 확정하지 못함 (자동 등록 금지)'

def main():
    with SOURCE.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    grouped={}
    for r in rows:grouped.setdefault(r['출처URL'],[]).append(r)
    for url,items in grouped.items():
        try:
            final,parts=fetch(url)
            if urlparse(final).path!=urlparse(url).path:
                raise ValueError('요청한 코스 페이지와 다른 주소로 이동')
            for r in items:
                pars=[int(r[f'PAR{i}']) for i in range(1,10)]
                r['상태'],r['검사메모']=inspect(parts,r['코스명'],pars)
        except Exception as exc:
            for r in items:r['상태']='접속/분석 보류';r['검사메모']=type(exc).__name__+': '+str(exc)[:120]
    with OUTPUT.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    for r in rows:print(f"{r['골프장명']}/{r['코스명']}: {r['상태']} — {r['검사메모']}")
    print('검토용 결과:',OUTPUT)
    return 0

if __name__=='__main__':sys.exit(main())

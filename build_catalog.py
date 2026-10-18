#!/usr/bin/env python3
"""Build a verified Ojang golf-course feed from human-reviewed course rows."""
import csv, json, re, sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'verified_courses.csv'
OUT = ROOT / 'catalog.json'
FIELDS = ['골프장명','지역','코스명'] + [f'PAR{i}' for i in range(1,10)] + ['출처URL','확인일']

def main():
    with SRC.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV 헤더가 템플릿과 다릅니다: '+str(reader.fieldnames))
        clubs=OrderedDict(); seen=set(); errors=[]; n=0
        for lineno,row in enumerate(reader,start=2):
            if not any((v or '').strip() for v in row.values() if isinstance(v,str)):
                continue
            name=(row['골프장명'] or '').strip(); region=(row['지역'] or '').strip(); course=(row['코스명'] or '').strip()
            if not name or not course or not region:
                errors.append(f'{lineno}행: 골프장명/지역/코스명 필수');continue
            key=(name,course)
            if key in seen: errors.append(f'{lineno}행: 중복 코스 {name}/{course}');continue
            seen.add(key)
            try:
                pars=[int((row[f'PAR{i}'] or '').strip()) for i in range(1,10)]
                if not all(3<=p<=6 for p in pars): raise ValueError('PAR는 3~6')
            except Exception:
                errors.append(f'{lineno}행: 9개 PAR는 모두 3~6 정수여야 합니다');continue
            source=(row['출처URL'] or '').strip(); checked=(row['확인일'] or '').strip()
            parsed=urlparse(source)
            if parsed.scheme!='https' or not parsed.netloc or not checked or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',checked):
                errors.append(f'{lineno}행: HTTPS 공식 출처URL과 확인일(YYYY-MM-DD) 필수');continue
            try: datetime.strptime(checked,'%Y-%m-%d')
            except ValueError: errors.append(f'{lineno}행: 유효하지 않은 확인일');continue
            if name not in clubs: clubs[name]={'name':name,'region':region,'courses':[]}
            if clubs[name]['region']!=region:
                errors.append(f'{lineno}행: 동일 골프장의 지역 불일치');continue
            clubs[name]['courses'].append({'name':course,'pars':pars})
            n+=1
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        print('검증 실패: catalog.json을 변경하지 않았습니다.',file=sys.stderr)
        return 1
    payload={'version':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'clubs':list(clubs.values())}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'검증 완료: 골프장 {len(clubs)}곳 / 코스 {n}개 → {OUT.name}')
    if not n: print('주의: 검증된 코스가 없어 앱 업데이트는 아직 수행되지 않습니다.')
    return 0
if __name__=='__main__': sys.exit(main())

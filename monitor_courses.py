#!/usr/bin/env python3
"""Monitor approved golf-club source pages; never infer or publish PAR values."""
import csv
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CSV = ROOT / 'verified_courses.csv'
STATE = ROOT / 'course_page_state.json'
NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def normalize(body):
    text = body.decode('utf-8', errors='replace')
    text = re.sub(r'(?is)<(script|style|noscript)\b[^>]*>.*?</\1\s*>', ' ', text)
    text = re.sub(r'(?s)<!--.*?-->', ' ', text)
    text = re.sub(r'(?s)<[^>]*>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch(url):
    req = Request(url, headers={'User-Agent': 'OJANG-Golf-Course-Monitor/1.0 (public-source-change-check)', 'Accept': 'text/html,application/xhtml+xml'})
    with urlopen(req, timeout=25) as resp:
        raw = resp.read(3_000_001)
        if len(raw) > 3_000_000:
            raise ValueError('Page exceeds 3 MB limit')
        return hashlib.sha256(normalize(raw).encode('utf-8')).hexdigest()


def main():
    with CSV.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or '출처URL' not in reader.fieldnames or '골프장명' not in reader.fieldnames:
            raise ValueError('CSV missing required columns')
        sources = {}
        for row in reader:
            url = (row.get('출처URL') or '').strip()
            club = (row.get('골프장명') or '').strip()
            if url and club:
                if not url.startswith('https://'):
                    raise ValueError('Non-HTTPS source rejected')
                sources.setdefault(url, set()).add(club)
    old = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'sources': {}}
    prev = old.get('sources', {})
    state = {}
    report = ['# 골프장 공식 출처 변경 감시', '', f'검사 시각(UTC): {NOW}', '']
    changes, failures = [], []
    for url, clubs in sorted(sources.items()):
        try:
            digest = fetch(url)
            previous = prev.get(url, {}).get('sha256')
            status = '변경 감지 — 수동 PAR 검증 필요' if previous and previous != digest else ('최초 기준값 저장' if not previous else '변경 없음')
            if previous and previous != digest:
                changes.append(url)
            state[url] = {'clubs': sorted(clubs), 'sha256': digest}
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            status = f'접속 실패 — 확인 필요 ({type(exc).__name__})'
            failures.append(url)
            if url in prev:
                state[url] = prev[url]  # preserve last successful baseline
        report.append(f'- **{", ".join(sorted(clubs))}**: {status}  ')
        report.append(f'  {url}')
    report += ['', f'변경 감지: {len(changes)}개 / 접속 실패: {len(failures)}개', '', '> 이 작업은 변경 여부만 확인합니다. PAR 데이터는 자동으로 수정하지 않습니다.']
    summary = '\n'.join(report) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(summary)
    STATE.write_text(json.dumps({'sources': state}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failures:
        print('WARNING: Some official sites could not be reached. Check Actions summary.', file=sys.stderr)
    return 0  # network issues do not break catalog publishing


if __name__ == '__main__':
    sys.exit(main())

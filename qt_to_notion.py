"""
동안교회 오늘의 큐티 → 노션 데이터베이스 자동 저장
- 매일 새벽 실행: 오늘 날짜(한국시간) 큐티를 찾아 노션에 새 페이지 생성
- 기존 노션 "My board › 큐티" 데이터베이스에 26/11/26 형식 제목으로 저장
- 필요 환경변수: NOTION_TOKEN, NOTION_DATABASE_ID (큐티 DB: 18fdc260f3ef804f9852e7f02f7b9700)
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
API = "https://www.dongan.org/api/qt/{}"
# 기준점: 2026-11-26 큐티의 글 번호. 글 번호가 날짜마다 1씩 늘어난다고 보고 오늘 번호를 추정한 뒤 주변을 찾아봄.
BASE_ID, BASE_DATE = 2068, date(2026, 11, 26)
SEARCH_RANGE = 15

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2022-06-28"


def http_json(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    req.add_header("User-Agent", "Mozilla/5.0 (QT-to-Notion)")
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def get_qt(qt_id):
    try:
        d = http_json(API.format(qt_id))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return None
    if not d or d.get("isDeleted") or not d.get("isVisible", 1):
        return None
    return d


def find_today_qt(today):
    guess = BASE_ID + (today - BASE_DATE).days
    prefix = today.isoformat()
    # 추정 번호부터 가까운 순서로 탐색
    for off in [0] + [s * i for i in range(1, SEARCH_RANGE + 1) for s in (1, -1)]:
        d = get_qt(guess + off)
        if d and str(d.get("title", "")).startswith(prefix):
            return d
    return None


def parse_content(html):
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&amp;", "&")
    parts = re.split(r"<본문 요약 및 해설>", text, maxsplit=1)
    body = parts[0].replace("<본문>", "").strip()
    commentary = parts[1].strip() if len(parts) > 1 else ""
    verses = [ln.strip() for ln in body.split("\n") if ln.strip()]
    return verses, commentary


def rt(text):
    # 노션 텍스트는 한 덩어리 2000자 제한
    return [{"type": "text", "text": {"content": text[i:i + 1900]}} for i in range(0, len(text), 1900)] or [
        {"type": "text", "text": {"content": ""}}]


def block(kind, text=""):
    return {"object": "block", "type": kind, kind: {"rich_text": rt(text)}}


def notion_title(today):
    # 기존 큐티 DB 제목 형식: 26/11/26 (연/월/일, 앞자리 0 없음)
    return f"{today.year % 100}/{today.month}/{today.day}"


def build_page(qt, today):
    title = qt["title"]
    passage = title[len(today.isoformat()):].strip() or title
    book = passage.split()[0] if passage else ""
    verses, commentary = parse_content(qt.get("content", ""))

    bold_title = {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": passage}, "annotations": {"bold": True}}]}}
    children = [bold_title]
    children += [block("paragraph", v) for v in verses]
    if commentary:
        children += [block("heading_3", "💡 본문 요약 및 해설"), block("quote", commentary)]
    children += [{"object": "block", "type": "divider", "divider": {}}, block("heading_3", "✍️ 나의 묵상")]
    for label in ["관찰 — 무엇이 눈에 띄었나", "깨달음 — 하나님은 어떤 분이신가", "적용 — 오늘 내가 할 일", "기도"]:
        children += [block("paragraph", label), block("paragraph", "")]

    tags = [{"name": "큐티"}] + ([{"name": book}] if book else [])
    props = {
        "이름": {"title": rt(notion_title(today))},
        "태그": {"multi_select": tags},
    }
    return {"parent": {"database_id": NOTION_DB}, "properties": props, "children": children[:100]}


def notion_headers():
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}


def already_saved(today):
    q = {"filter": {"property": "이름", "title": {"equals": notion_title(today)}}, "page_size": 1}
    res = http_json(f"https://api.notion.com/v1/databases/{NOTION_DB}/query", "POST", q, notion_headers())
    return bool(res.get("results"))


def main():
    today = datetime.now(KST).date()
    if len(sys.argv) > 1:  # 특정 날짜 수동 실행: python qt_to_notion.py 2026-11-26
        today = date.fromisoformat(sys.argv[1])
    if not NOTION_TOKEN or not NOTION_DB:
        sys.exit("NOTION_TOKEN / NOTION_DATABASE_ID 환경변수가 필요합니다.")
    # 1) 동안교회 게시물 제목에서 오늘 본문 찾기 (노션보다 먼저 확인)
    qt = find_today_qt(today)
    if not qt:
        sys.exit(f"[교회] {today} 큐티를 찾지 못했습니다. (아직 게시 전이거나 글 번호 규칙이 바뀌었을 수 있음)")
    print("[교회] 오늘 큐티 찾음:", qt["title"])
    # 2) 노션에 이미 있는지 확인
    try:
        if already_saved(today):
            print(f"[노션] {notion_title(today)} 페이지가 이미 있습니다. 건너뜀.")
            return
    except urllib.error.HTTPError as e:
        sys.exit(f"[노션] 데이터베이스 접근 실패 ({e.code}): {e.read().decode('utf-8', 'ignore')[:300]}\n"
                 "→ 404면 노션 '큐티 자동저장' 통합이 My board/큐티에 연결되지 않았거나 DB ID가 다릅니다.")
    page = http_json("https://api.notion.com/v1/pages", "POST", build_page(qt, today), notion_headers())
    print("저장 완료:", qt["title"], page.get("url"))


if __name__ == "__main__":
    main()

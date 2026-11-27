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


LIST_API = "https://www.dongan.org/api/qt?page=1&pageSize=10&keyword="


def _find_items(obj):
    """목록 응답 구조가 무엇이든 title이 있는 글 목록을 찾아냄"""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any("title" in x for x in obj):
            return obj
        for x in obj:
            r = _find_items(x)
            if r:
                return r
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _find_items(v)
            if r:
                return r
    return []


LIST_API = "https://www.dongan.org/api/qt?page=1&pageSize=10&keyword="


def _find_items(obj):
    """목록 응답에서 title 이 들어있는 글 목록을 찾아냄 (응답 구조가 달라도 동작)"""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any("title" in x for x in obj):
            return obj
        for x in obj:
            r = _find_items(x)
            if r:
                return r
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _find_items(v)
            if r:
                return r
    return []


def find_today_qt(today):
    prefix = today.isoformat()
    # 1) 큐티 게시판 목록에서 제목이 오늘 날짜로 시작하는 글 찾기
    try:
        items = _find_items(http_json(LIST_API))
        print(f"[교회] 목록에서 {len(items)}개 글 확인")
        for it in items:
            if str(it.get("title", "")).startswith(prefix):
                if it.get("content"):
                    return it
                d = get_qt(it.get("id"))
                if d:
                    return d
    except Exception as e:  # noqa
        print("[교회] 목록 읽기 실패:", repr(e))
    # 2) 예비: 글 번호 추정 방식
    guess = BASE_ID + (today - BASE_DATE).days
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

    schema = db_schema()
    title_prop = next((k for k, v in schema.items() if v.get("type") == "title"), "이름")
    props = {title_prop: {"title": rt(notion_title(today))}}
    for name, v in schema.items():  # 데이터베이스에 있는 열만 채움
        t = v.get("type")
        if t == "multi_select" and name == "태그":
            props[name] = {"multi_select": [{"name": "큐티"}] + ([{"name": book}] if book else [])}
        elif t == "date" and name == "날짜":
            props[name] = {"date": {"start": today.isoformat()}}
        elif t == "rich_text" and name == "본문":
            props[name] = {"rich_text": rt(passage)}
        elif t == "url" and name.replace(" ", "") == "원문링크":
            props[name] = {"url": "https://www.dongan.org/dongan/page/s0506.html"}
    return {"parent": {"database_id": NOTION_DB}, "properties": props, "children": children[:100]}


_SCHEMA = None


def db_schema():
    """노션 데이터베이스의 열 목록 (열 이름이 달라도 맞춰서 저장하기 위함)"""
    global _SCHEMA
    if _SCHEMA is None:
        d = http_json(f"https://api.notion.com/v1/databases/{NOTION_DB}", headers=notion_headers())
        _SCHEMA = d.get("properties", {})
        print("[노션] 데이터베이스:", "".join(t.get("plain_text", "") for t in d.get("title", [])),
              "/ 열:", ", ".join(_SCHEMA))
    return _SCHEMA


def notion_headers():
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}


def already_saved(today):
    title_prop = next((k for k, v in db_schema().items() if v.get("type") == "title"), "이름")
    q = {"filter": {"property": title_prop, "title": {"equals": notion_title(today)}}, "page_size": 1}
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

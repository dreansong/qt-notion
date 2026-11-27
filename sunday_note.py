"""
동안교회 주보(이미지) → 노션 '예배기록'에 주일 예배노트 자동 생성
- 매주 일요일 10시: 이번 주일 주보 이미지를 받아 글자 인식(OCR) → 설교 제목/설교자/성경봉독 추출
- 필요: tesseract-ocr(kor), poppler-utils(PDF일 때)
- 환경변수: NOTION_TOKEN, NOTION_WORSHIP_DB_ID(없으면 예배기록 기본값)
"""
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, date

import qt_to_notion as q  # http_json, rt, block, todo, notion_headers, KST 재사용

SITE = "https://www.dongan.org"
LIST_API = SITE + "/api/bulletins?page=1&pageSize=10&keyword="
q.NOTION_DB = os.environ.get("NOTION_WORSHIP_DB_ID", "18fdc260f3ef80689f35c348d1726725")

BOOKS = {  # 성경 이름 → 대한성서공회 약어 (본문 읽기 링크용)
    "창세기": "gen", "출애굽기": "exo", "레위기": "lev", "민수기": "num", "신명기": "deu", "여호수아": "jos",
    "사사기": "jdg", "룻기": "rut", "사무엘상": "1sa", "사무엘하": "2sa", "열왕기상": "1ki", "열왕기하": "2ki",
    "역대상": "1ch", "역대하": "2ch", "에스라": "ezr", "느헤미야": "neh", "에스더": "est", "욥기": "job",
    "시편": "psa", "잠언": "pro", "전도서": "ecc", "아가": "sng", "이사야": "isa", "예레미야": "jer",
    "예레미야애가": "lam", "에스겔": "ezk", "다니엘": "dan", "호세아": "hos", "요엘": "jol", "아모스": "amo",
    "오바댜": "oba", "요나": "jnh", "미가": "mic", "나훔": "nam", "하박국": "hab", "스바냐": "zep", "학개": "hag",
    "스가랴": "zec", "말라기": "mal", "마태복음": "mat", "마가복음": "mrk", "누가복음": "luk", "요한복음": "jhn",
    "사도행전": "act", "로마서": "rom", "고린도전서": "1co", "고린도후서": "2co", "갈라디아서": "gal",
    "에베소서": "eph", "빌립보서": "php", "골로새서": "col", "데살로니가전서": "1th", "데살로니가후서": "2th",
    "디모데전서": "1ti", "디모데후서": "2ti", "디도서": "tit", "빌레몬서": "phm", "히브리서": "heb",
    "야고보서": "jas", "베드로전서": "1pe", "베드로후서": "2pe", "요한일서": "1jn", "요한이서": "2jn",
    "요한삼서": "3jn", "유다서": "jud", "요한계시록": "rev"}


def this_sunday(today):
    return today + timedelta(days=(6 - today.weekday()) % 7)


def find_bulletin(sunday):
    want = re.compile(rf"{sunday.month}\s*월\s*{sunday.day}\s*일")
    items = q._find_items(q.http_json(LIST_API))
    for it in items:
        if want.search(str(it.get("title", ""))):
            return q.http_json(f"{SITE}/api/bulletins/{it['id']}")
    return None


def file_urls(obj):
    """주보 JSON 안의 이미지/PDF 주소를 모두 찾음"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            for m in re.findall(r"[^\s\"'<>,]+\.(?:jpe?g|png|gif|webp|pdf)", o, re.I):
                out.append(m if m.startswith("http") else SITE + ("" if m.startswith("/") else "/") + m)
    walk(obj)
    return list(dict.fromkeys(out))


def ocr_images(urls, workdir):
    from PIL import Image
    texts = []
    for i, u in enumerate(urls):
        path = os.path.join(workdir, f"f{i}" + os.path.splitext(u.split("?")[0])[1].lower())
        req = urllib.request.Request(urllib.parse.quote(u, safe=":/?=&%"), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            f.write(r.read())
        pages = [path]
        if path.endswith(".pdf"):
            subprocess.run(["pdftoppm", "-r", "200", "-png", path, path[:-4]], check=True)
            pages = sorted(p for p in (os.path.join(workdir, x) for x in os.listdir(workdir))
                           if p.startswith(path[:-4] + "-") and p.endswith(".png"))
        for p in pages:
            im = Image.open(p).convert("L")
            if im.width < 1800:  # 작은 이미지는 키워서 인식률 개선
                im = im.resize((im.width * 2, im.height * 2))
            for part, box in (("left", (0, 0, int(im.width * 0.6), im.height)), ("full", None)):
                src = im.crop(box) if box else im
                fn = f"{p}.{part}.png"
                src.save(fn)
                r = subprocess.run(["tesseract", fn, "stdout", "-l", "kor+eng", "--psm", "6"],
                                   capture_output=True, text=True)
                texts.append((part, r.stdout))
    return texts


def fix_ref(ref):
    ref = ref.replace(";", ":").replace("~", "-").strip(" .,…-")
    # OCR이 ':'를 빠뜨린 경우 (예: 창세기 461-7 → 창세기 46:1-7)
    m = re.match(r"([가-힣]+)\s*(\d{3,4})(-.*)?$", ref.split(",")[0].strip())
    if m and ":" not in ref:
        num = m.group(2)
        cut = 3 if (m.group(1) == "시편" and len(num) == 4) else 2
        first = f"{m.group(1)} {num[:cut]}:{num[cut:]}{m.group(3) or ''}"
        ref = ", ".join([first] + [x.strip() for x in ref.split(",")[1:]])
    return re.sub(r"\s+", " ", ref)


def parse(texts):
    info = {}
    ordered = [t for p, t in texts if p == "left"] + [t for p, t in texts if p == "full"]
    for t in ordered:
        for line in t.splitlines():
            s = line.replace("…", " ").replace("·", " ")
            if "reading" not in info:
                m = re.search(r"성\s*경\s*봉\s*독\W*([가-힣]{2,7})\s*([0-9][0-9:;,\-~ ]*[0-9])", s)
                if m and m.group(1) in BOOKS:
                    info["book"], info["reading"] = m.group(1), fix_ref(f"{m.group(1)} {m.group(2)}")
            if "title" not in info:
                m = re.search(r"(?:말|망|맡|밀)?\s*씀\s+(.+?)\s*[\-…~ ]*([가-힣]{2,4})\s*목사", s)
                if m and len(m.group(1)) >= 3:
                    info["title"], info["preacher"] = m.group(1).strip(" .-~"), m.group(2) + " 목사"
    return info


def build_page(sunday, info, img_urls, raw):
    t = f"{sunday.year % 100}/{sunday.month}/{sunday.day}"
    reading = info.get("reading", "(주보에서 확인)")
    link = ""
    if info.get("book"):
        ch = re.match(r"\D*(\d+)", reading[len(info["book"]):])
        if ch:
            link = (f"https://www.bskorea.or.kr/bible/korbibReadpage.php?version=GAE"
                    f"&book={BOOKS[info['book']]}&chap={int(ch.group(1)[:3])}")

    def line(label, value, url=None):
        rich = [{"type": "text", "text": {"content": label + "  "}, "annotations": {"bold": True}},
                {"type": "text", "text": {"content": value}}]
        if url:
            rich.append({"type": "text", "text": {"content": "  📖 본문 읽기", "link": {"url": url}}})
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}

    ch = [q.block("heading_3", "📋 예배 정보"),
          line("날짜", f"{sunday.year}년 {sunday.month}월 {sunday.day}일 주일예배"),
          line("설교", info.get("title", "(주보에서 확인)")),
          line("설교자", info.get("preacher", "")),
          line("성경봉독", reading, link or None)]
    if img_urls:
        ch.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "🗞️ 이번 주 주보 보기", "link": {"url": img_urls[0]}}}]}})
    ch.append(q.block("quote", "주보 이미지를 자동으로 읽은 내용이라 글자가 틀릴 수 있어요. 예배 전에 한 번 확인해 주세요."))
    ch += [{"object": "block", "type": "divider", "divider": {}},
           q.block("heading_3", "📖 본문 말씀"), q.block("paragraph", reading), q.block("paragraph", ""),
           q.block("heading_3", "🗒️ 설교 노트")]
    for n in ("1.", "2.", "3.", "메모"):
        ch += [q.block("paragraph", n), q.block("paragraph", "")]
    ch += [q.block("heading_3", "💡 핵심 메시지"), q.block("quote", ""),
           q.block("heading_3", "🙏 삶의 적용 (이번 주 실천)")] + [q.todo("") for _ in range(3)]
    ch += [q.block("heading_3", "기도"), q.block("paragraph", "")]
    props = {"이름": {"title": q.rt(t)}, "태그": {"multi_select": [{"name": "주일설교"}]}}
    return t, {"parent": {"database_id": q.NOTION_DB}, "icon": {"type": "emoji", "emoji": "⛪"},
               "properties": props, "children": ch}


def main():
    today = datetime.now(q.KST).date()
    if len(sys.argv) > 1:
        today = date.fromisoformat(sys.argv[1])
    sunday = this_sunday(today)
    b = find_bulletin(sunday)
    if not b:
        sys.exit(f"[주보] {sunday.month}월 {sunday.day}일 주보를 찾지 못했습니다.")
    urls = file_urls(b)
    print(f"[주보] {b.get('title')} / 파일 {len(urls)}개")
    with tempfile.TemporaryDirectory() as d:
        texts = ocr_images(urls, d) if urls else []
    info = parse(texts)
    print("[주보] 인식 결과:", info)
    title, page = build_page(sunday, info, urls, texts)
    res = q.http_json(f"https://api.notion.com/v1/databases/{q.NOTION_DB}/query", "POST",
                      {"filter": {"property": "이름", "title": {"equals": title}}, "page_size": 1},
                      q.notion_headers())
    if res.get("results"):
        print(f"[노션] {title} 예배노트가 이미 있습니다. 건너뜀.")
        return
    r = q.http_json("https://api.notion.com/v1/pages", "POST", page, q.notion_headers())
    print("[노션] 예배노트 생성:", r.get("url"))


if __name__ == "__main__":
    import urllib.parse  # noqa
    main()

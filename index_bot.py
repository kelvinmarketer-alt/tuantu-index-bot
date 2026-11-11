#!/usr/bin/env python3
"""
tuantu-index-bot
----------------
Mỗi sáng:
  1. Lấy danh sách URL từ sitemap website (tự động).
  2. Kiểm tra trạng thái index qua Google Search Console URL Inspection API.
  3. Ghi báo cáo (theo ngày) vào Google Sheet.
  4. URL nào CHƯA index -> tự submit lại qua Google Indexing API (ép index nhanh).
  5. Gửi tóm tắt vào nhóm Telegram.

Chạy: python index_bot.py
Tất cả cấu hình lấy từ biến môi trường (xem .env.example / README).
"""
import os
import sys
import json
import time
import datetime
import xml.etree.ElementTree as ET

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ----------------------------- Cấu hình -----------------------------
_raw_site = os.environ["SITE_URL"].strip()                 # PHẢI khớp property trong GSC
# Domain property dùng "sc-domain:domain.com"; URL-prefix thì thêm "/" cuối
SITE_URL = _raw_site if _raw_site.startswith("sc-domain:") else _raw_site.rstrip("/") + "/"
SITEMAP_URL = os.environ.get("SITEMAP_URL", SITE_URL + "sitemap.xml")
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Index Report")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SA_JSON = os.environ["GCP_SA_JSON"]                         # nội dung JSON của service account
MAX_URLS = int(os.environ.get("MAX_URLS", "200"))          # trần URL kiểm tra mỗi lần
DO_SUBMIT = os.environ.get("DO_SUBMIT_INDEXING", "true").lower() == "true"
SUBMIT_LIMIT = int(os.environ.get("SUBMIT_LIMIT", "180"))  # Indexing API free = 200/ngày, chừa headroom
TZ_OFFSET = int(os.environ.get("TZ_OFFSET_HOURS", "7"))    # giờ VN = UTC+7
# Nguồn lấy danh sách URL: auto (sitemap trước, không được thì đọc tab Sheet) | sitemap | sheet
URL_SOURCE = os.environ.get("URL_SOURCE", "auto").lower()
URLS_TAB = os.environ.get("URLS_TAB", "URLs")              # tab chứa danh sách URL (cột A)
# FORCE_URLS: các URL LUÔN ép index (dù đã index) để buộc Google crawl lại — vd trang chủ,
# bài vừa đổi title. Phân tách bằng xuống dòng hoặc dấu phẩy. Được ưu tiên submit trước.
FORCE_URLS = [u.strip() for u in os.environ.get("FORCE_URLS", "").replace(",", "\n").splitlines()
              if u.strip().startswith("http")]
DUMP_JSON = os.environ.get("DUMP_JSON", "")                # xuất kết quả audit ra file JSON (tùy chọn)
URLS_FILE = os.environ.get("URLS_FILE", "")                # nếu set: đọc danh sách URL từ file (bỏ sitemap)
SLEEP_SEC = float(os.environ.get("SLEEP_SEC", "0.4"))      # giãn nhịp giữa các lần inspect (tránh cạn cổng)

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",  # URL Inspection
    "https://www.googleapis.com/auth/indexing",             # Indexing API
    "https://www.googleapis.com/auth/spreadsheets",         # ghi Sheet
]

# Trạng thái coi là "đã index"
INDEXED_STATES = {"Submitted and indexed", "Indexed, not submitted in sitemap"}


def today_str():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=TZ_OFFSET)
    return now.strftime("%Y-%m-%d")


def get_credentials():
    info = json.loads(SA_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


# Header giống trình duyệt để né WAF "One moment" / lỗi 415 của .vn
SITEMAP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.9",
}


def _fetch_xml(url):
    """Tải sitemap có retry; bỏ qua trang challenge WAF."""
    last = None
    for i in range(3):
        try:
            r = requests.get(url, timeout=30, headers=SITEMAP_HEADERS)
            head = r.content[:600]
            if r.status_code == 200 and b"<" in head and b"One moment" not in head:
                return r.content
            last = f"HTTP {r.status_code}" + (" (WAF challenge)" if b"One moment" in head else "")
        except Exception as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))
    print(f"[warn] không tải được {url}: {last}")
    return None


def get_sitemap_urls(sitemap_url, _depth=0):
    """Đọc sitemap (hỗ trợ cả sitemap index lồng nhau)."""
    urls = []
    if _depth > 5:
        return urls
    content = _fetch_xml(sitemap_url)
    if not content:
        return urls
    try:
        root = ET.fromstring(content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        children = root.findall(".//sm:sitemap/sm:loc", ns)
        if children:  # đây là sitemap index -> đệ quy
            for c in children:
                urls += get_sitemap_urls(c.text.strip(), _depth + 1)
        else:
            for loc in root.findall(".//sm:url/sm:loc", ns):
                urls.append(loc.text.strip())
    except Exception as e:
        print(f"[warn] parse sitemap {sitemap_url}: {e}")
    return urls


def get_urls_from_sheet(sheets):
    """Đọc danh sách URL từ cột A của tab URLS_TAB (bỏ dòng tiêu đề)."""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{URLS_TAB}!A2:A"
        ).execute()
        return [r[0].strip() for r in res.get("values", [])
                if r and r[0].strip().startswith("http")]
    except Exception as e:
        print(f"[warn] không đọc được tab '{URLS_TAB}': {e}")
        return []


def persist_urls_to_tab(sheets, urls):
    """Lưu danh sách URL vào tab dự phòng (ghi lô để khỏi rớt SSL)."""
    try:
        rows = [["URL"]] + [[u] for u in urls]
        for start in range(0, len(rows), 150):
            sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range=f"{URLS_TAB}!A{start + 1}",
                valueInputOption="RAW", body={"values": rows[start:start + 150]},
            ).execute()
    except Exception as e:
        print(f"[warn] lưu tab '{URLS_TAB}' lỗi: {type(e).__name__}")


def get_target_urls(sheets):
    """Sitemap trước; lấy được thì lưu vào tab dự phòng. Sitemap lỗi (WAF) -> đọc tab."""
    urls = []
    if URL_SOURCE in ("sitemap", "auto"):
        urls = get_sitemap_urls(SITEMAP_URL)
        if urls:
            print(f"[info] {len(urls)} URL lấy từ sitemap")
            persist_urls_to_tab(sheets, urls)
            return urls
    if URL_SOURCE in ("sheet", "auto"):
        urls = get_urls_from_sheet(sheets)
        print(f"[info] {len(urls)} URL lấy từ tab '{URLS_TAB}' (dự phòng)")
    return urls


_err_shown = 0  # in vài lỗi đầu để chẩn đoán (rate-limit? phân quyền?)


def inspect_url(sc, url):
    """Trả về (coverageState, verdict)."""
    global _err_shown
    body = {"inspectionUrl": url, "siteUrl": SITE_URL, "languageCode": "en-US"}
    try:
        res = sc.urlInspection().index().inspect(body=body).execute()
        r = res.get("inspectionResult", {}).get("indexStatusResult", {})
        return r.get("coverageState", "Unknown"), r.get("verdict", "")
    except HttpError as e:
        if _err_shown < 4:
            print(f"[inspect-err] {e.resp.status}: {str(e)[:220]}")
            _err_shown += 1
        return f"ERROR {e.resp.status}", ""
    except Exception as e:
        if _err_shown < 4:
            print(f"[inspect-err] {type(e).__name__}: {str(e)[:220]}")
            _err_shown += 1
        return f"ERROR {type(e).__name__}", ""


def submit_indexing(idx, url):
    """Trả 'ok' | 'quota' (hết quota ngày) | 'err'."""
    try:
        idx.urlNotifications().publish(
            body={"url": url, "type": "URL_UPDATED"}
        ).execute()
        return "ok"
    except HttpError as e:
        if e.resp.status == 429:
            return "quota"
        print(f"[warn] submit indexing lỗi {e.resp.status}: {url}")
        return "err"
    except Exception as e:
        print(f"[warn] submit indexing lỗi {type(e).__name__}: {url}")
        return "err"


# Layout ma trận: mỗi URL 1 hàng, mỗi lần chạy thêm 1 cột ngày (icon trạng thái)
BASE_HEADERS = ["URL", "Trạng thái gần nhất", "Cập nhật lúc"]


def ensure_tab(sheets):
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if SHEET_TAB not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]},
        ).execute()


def write_matrix(creds, results, day):
    """Đọc lưới hiện có -> cập nhật trạng thái -> ghi đè (gọn, không trùng hàng theo ngày)."""
    # Dựng client Sheet MỚI ngay tại đây (run dài làm kết nối cũ bị stale -> SSL EOF)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    grid = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:ZZ"
    ).execute().get("values", [])
    if not grid:
        grid = [list(BASE_HEADERS)]

    header = grid[0]
    for i, h in enumerate(BASE_HEADERS):       # đảm bảo 3 cột cố định
        if i >= len(header):
            header.append(h)
        else:
            header[i] = h
    if day in header:                          # cột lịch sử cho hôm nay
        col = header.index(day)
    else:
        col = len(header)
        header.append(day)
    grid[0] = header
    width = len(header)

    rowmap = {row[0]: idx for idx, row in enumerate(grid)
              if idx > 0 and row and row[0]}

    def fit(r):
        return r + [""] * (width - len(r)) if len(r) < width else r

    for url, state, icon in results:
        if url in rowmap:
            r = fit(grid[rowmap[url]])
        else:
            r = fit([url])
            grid.append(r)
            rowmap[url] = len(grid) - 1
        r[0], r[1], r[2], r[col] = url, state, day, icon
        grid[rowmap[url]] = r

    grid = [fit(r) for r in grid]
    # Ghi theo lô nhỏ để tránh rớt SSL khi đẩy payload lớn; mỗi lô tự retry
    CHUNK = 150
    for start in range(0, len(grid), CHUNK):
        block = grid[start:start + CHUNK]
        a1 = f"{SHEET_TAB}!A{start + 1}"
        for attempt in range(4):
            try:
                sheets.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID, range=a1,
                    valueInputOption="RAW", body={"values": block},
                ).execute()
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"[warn] ghi {a1} lỗi, thử lại {attempt + 1}/3: {type(e).__name__}")
                time.sleep(3 * (attempt + 1))
                sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[info] bỏ qua Telegram (chưa cấu hình token/chat_id)")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
    except Exception as e:
        print(f"[warn] gửi Telegram lỗi: {e}")


def main():
    day = today_str()
    creds = get_credentials()
    sc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    idx = build("indexing", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Nguồn URL: file (nếu có) để chạy dọn tập nhỏ, ngược lại lấy sitemap
    if URLS_FILE and os.path.exists(URLS_FILE):
        with open(URLS_FILE, encoding="utf-8") as f:
            base = [ln.strip() for ln in f if ln.strip().startswith("http")]
        print(f"[info] {len(base)} URL từ URLS_FILE={URLS_FILE}")
    else:
        base = get_target_urls(sheets)
    # FORCE_URLS lên đầu để chắc chắn được submit trước khi cạn quota
    urls = list(dict.fromkeys(FORCE_URLS + base))[:MAX_URLS]
    force_set = set(FORCE_URLS)
    if not urls:
        send_telegram(
            f"⚠️ <b>Index bot</b> ({day})\nChưa có URL để kiểm tra. "
            f"Hãy dán link bài vào tab '<b>{URLS_TAB}</b>' của Sheet, hoặc mở tường chặn cho sitemap."
        )
        print("[stop] không có URL nào")
        sys.exit(0)

    ensure_tab(sheets)

    results = []
    n_ok = n_no = n_err = n_submit = 0
    not_indexed = []
    quota_hit = False

    n_force = 0
    for i, url in enumerate(urls, 1):
        state, verdict = inspect_url(sc, url)
        forced = url in force_set
        if state.startswith("ERROR"):
            icon = "🔴"
            n_err += 1
        elif state in INDEXED_STATES:
            icon = "✅"
            n_ok += 1
        else:
            icon = "🟡"
            n_no += 1
            not_indexed.append((url, state))
        # Ép index: URL trong FORCE_URLS ép DÙ đã index (buộc crawl lại); còn lại ép khi chưa index
        want_submit = (forced or icon == "🟡") and not state.startswith("ERROR")
        if DO_SUBMIT and want_submit and not quota_hit and n_submit < SUBMIT_LIMIT:
            r = submit_indexing(idx, url)
            if r == "ok":
                icon += "🚀"
                n_submit += 1
                if forced:
                    n_force += 1
            elif r == "quota":
                quota_hit = True   # hết quota ngày -> ngừng ép, chờ mai
        results.append((url, state, icon))
        if i % 20 == 0:
            print(f"  ...{i}/{len(urls)}")
        time.sleep(SLEEP_SEC)  # nương theo quota URL Inspection (~600/phút)

    sheet_ok = True
    try:
        write_matrix(creds, results, day)
    except Exception as e:
        sheet_ok = False
        print(f"[error] không ghi được Sheet sau khi thử lại: {e}")

    if DUMP_JSON:
        try:
            with open(DUMP_JSON, "w", encoding="utf-8") as f:
                json.dump([{"url": u, "state": s, "icon": ic} for u, s, ic in results],
                          f, ensure_ascii=False)
            print(f"[info] dump {len(results)} kết quả audit -> {DUMP_JSON}")
        except Exception as e:
            print(f"[warn] dump json lỗi: {type(e).__name__}: {e}")

    # ---- Tóm tắt Telegram ----
    lines = [
        f"📊 <b>Báo cáo Index — {day}</b>",
        f"🌐 {SITE_URL.replace('sc-domain:', '')}",
        f"✅ Đã index: <b>{n_ok}</b>/{len(urls)}",
        f"🟡 Chưa index: <b>{n_no}</b>",
        f"🔴 Lỗi/Unknown: {n_err}",
        f"🚀 Vừa ép index lại: {n_submit}" + (f" (trong đó {n_force} URL ép cưỡng bức)" if n_force else ""),
    ]
    if quota_hit or n_no > n_submit:
        lines.append(f"⏳ Quota ép index 200/ngày — còn <b>{n_no - n_submit}</b> URL chờ ép các ngày sau")
    if not sheet_ok:
        lines.append("⚠️ Ghi Google Sheet lỗi mạng — số liệu vẫn đúng, sẽ ghi lại lần chạy sau")
    if not_indexed:
        lines.append("\n<b>Một số URL chưa index:</b>")
        for url, state in not_indexed[:10]:
            lines.append(f"• {url} — {state}")
        if len(not_indexed) > 10:
            lines.append(f"… và {len(not_indexed) - 10} URL khác (xem Sheet)")
    send_telegram("\n".join(lines))
    print(f"[done] ok={n_ok} no={n_no} err={n_err} submit={n_submit}")


if __name__ == "__main__":
    main()

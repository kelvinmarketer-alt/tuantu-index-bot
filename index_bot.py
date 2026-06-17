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
TZ_OFFSET = int(os.environ.get("TZ_OFFSET_HOURS", "7"))    # giờ VN = UTC+7
# Nguồn lấy danh sách URL: auto (sitemap trước, không được thì đọc tab Sheet) | sitemap | sheet
URL_SOURCE = os.environ.get("URL_SOURCE", "auto").lower()
URLS_TAB = os.environ.get("URLS_TAB", "URLs")              # tab chứa danh sách URL (cột A)

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


def get_sitemap_urls(sitemap_url, _depth=0):
    """Đọc sitemap (hỗ trợ cả sitemap index lồng nhau)."""
    urls = []
    if _depth > 5:
        return urls
    try:
        r = requests.get(sitemap_url, timeout=30, headers={"User-Agent": "tuantu-index-bot"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        children = root.findall(".//sm:sitemap/sm:loc", ns)
        if children:  # đây là sitemap index -> đệ quy
            for c in children:
                urls += get_sitemap_urls(c.text.strip(), _depth + 1)
        else:
            for loc in root.findall(".//sm:url/sm:loc", ns):
                urls.append(loc.text.strip())
    except Exception as e:
        print(f"[warn] không đọc được sitemap {sitemap_url}: {e}")
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


def get_target_urls(sheets):
    """Lấy URL theo URL_SOURCE. Site có tường challenge (vd .vn) -> dùng tab Sheet."""
    urls = []
    if URL_SOURCE in ("sitemap", "auto"):
        urls = get_sitemap_urls(SITEMAP_URL)
        if urls:
            print(f"[info] {len(urls)} URL lấy từ sitemap")
    if not urls and URL_SOURCE in ("sheet", "auto"):
        urls = get_urls_from_sheet(sheets)
        print(f"[info] {len(urls)} URL lấy từ tab '{URLS_TAB}'")
    return urls


def inspect_url(sc, url):
    """Trả về (coverageState, verdict)."""
    body = {"inspectionUrl": url, "siteUrl": SITE_URL, "languageCode": "en-US"}
    try:
        res = sc.urlInspection().index().inspect(body=body).execute()
        r = res.get("inspectionResult", {}).get("indexStatusResult", {})
        return r.get("coverageState", "Unknown"), r.get("verdict", "")
    except HttpError as e:
        return f"ERROR {e.resp.status}", ""
    except Exception as e:
        return f"ERROR {type(e).__name__}", ""


def submit_indexing(idx, url):
    try:
        idx.urlNotifications().publish(
            body={"url": url, "type": "URL_UPDATED"}
        ).execute()
        return True
    except Exception as e:
        print(f"[warn] submit indexing thất bại {url}: {e}")
        return False


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


def write_matrix(sheets, results, day):
    """Đọc lưới hiện có -> cập nhật trạng thái -> ghi đè (gọn, không trùng hàng theo ngày)."""
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
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1",
        valueInputOption="RAW", body={"values": grid},
    ).execute()


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

    urls = list(dict.fromkeys(get_target_urls(sheets)))[:MAX_URLS]
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

    for i, url in enumerate(urls, 1):
        state, verdict = inspect_url(sc, url)
        if state in INDEXED_STATES:
            icon = "✅"
            n_ok += 1
        elif state.startswith("ERROR"):
            icon = "🔴"
            n_err += 1
        else:
            n_no += 1
            not_indexed.append((url, state))
            icon = "🟡"
            if DO_SUBMIT and submit_indexing(idx, url):
                icon = "🟡🚀"   # chưa index -> đã ép lại
                n_submit += 1
        results.append((url, state, icon))
        if i % 20 == 0:
            print(f"  ...{i}/{len(urls)}")
        time.sleep(0.4)  # nương theo quota URL Inspection (~600/phút)

    write_matrix(sheets, results, day)

    # ---- Tóm tắt Telegram ----
    lines = [
        f"📊 <b>Báo cáo Index — {day}</b>",
        f"🌐 {SITE_URL}",
        f"✅ Đã index: <b>{n_ok}</b>/{len(urls)}",
        f"🟡 Chưa index: <b>{n_no}</b>",
        f"🔴 Lỗi/Unknown: {n_err}",
        f"🚀 Vừa ép index lại: {n_submit}",
    ]
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

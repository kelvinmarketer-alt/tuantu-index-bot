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
SITE_URL = os.environ["SITE_URL"].rstrip("/") + "/"        # PHẢI khớp property trong GSC
SITEMAP_URL = os.environ.get("SITEMAP_URL", SITE_URL + "sitemap.xml")
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Index Report")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SA_JSON = os.environ["GCP_SA_JSON"]                         # nội dung JSON của service account
MAX_URLS = int(os.environ.get("MAX_URLS", "200"))          # trần URL kiểm tra mỗi lần
DO_SUBMIT = os.environ.get("DO_SUBMIT_INDEXING", "true").lower() == "true"
TZ_OFFSET = int(os.environ.get("TZ_OFFSET_HOURS", "7"))    # giờ VN = UTC+7

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


def inspect_url(sc, url):
    """Trả về (coverageState, verdict)."""
    body = {"inspectionUrl": url, "siteUrl": SITE_URL, "languageCode": "vi-VN"}
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


def ensure_tab_and_header(sheets):
    """Tạo tab nếu chưa có + ghi header."""
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if SHEET_TAB not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]},
        ).execute()
    # đảm bảo header
    header = ["Ngày", "URL", "Trạng thái index", "Verdict", "Đã submit lại"]
    rng = f"{SHEET_TAB}!A1:E1"
    cur = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=rng).execute()
    if not cur.get("values"):
        sheets.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=rng,
            valueInputOption="RAW", body={"values": [header]},
        ).execute()


def append_rows(sheets, rows):
    if not rows:
        return
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
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

    urls = list(dict.fromkeys(get_sitemap_urls(SITEMAP_URL)))[:MAX_URLS]
    print(f"[info] {len(urls)} URL từ sitemap")
    if not urls:
        send_telegram(f"⚠️ <b>Index bot</b> ({day})\nKhông lấy được URL từ sitemap: {SITEMAP_URL}")
        sys.exit(0)

    ensure_tab_and_header(sheets)

    rows = []
    n_ok = n_no = n_err = n_submit = 0
    not_indexed = []

    for i, url in enumerate(urls, 1):
        state, verdict = inspect_url(sc, url)
        is_indexed = state in INDEXED_STATES
        submitted = ""
        if is_indexed:
            n_ok += 1
        elif state.startswith("ERROR"):
            n_err += 1
        else:
            n_no += 1
            not_indexed.append((url, state))
            if DO_SUBMIT and submit_indexing(idx, url):
                submitted = "✅"
                n_submit += 1
        rows.append([day, url, state, verdict, submitted])
        if i % 20 == 0:
            print(f"  ...{i}/{len(urls)}")
        time.sleep(0.4)  # nương theo quota URL Inspection (~600/phút)

    append_rows(sheets, rows)

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

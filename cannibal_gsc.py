#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cannibal_gsc.py — Đối chiếu Google Search Console để XÁC NHẬN ăn thịt từ khóa.

Ý tưởng: 1 truy vấn mà Google cho ≥2 URL của mình cùng nhận hiển thị (impressions)
= 2 trang đang đấu nhau cho cùng keyword → chia lực, kẹt hạng.

Chạy bằng chính service account của bot index (scope webmasters.readonly đủ để đọc
Search Analytics). Ghi kết quả ra 1 tab trong Google Sheet.

ENV:
  GCP_SA_JSON      nội dung JSON service account (1 dòng) — dùng chung secret bot index
  SITE_URL         property GSC (vd https://nongsantuantuhanoi.vn/ hoặc sc-domain:...)
  CANNIBAL_SHEET_ID  sheet để ghi (mặc định = SHEET_ID của bot)
  CANNIBAL_TAB     tên tab (mặc định 'GSC Ăn Thịt')
  DAYS             số ngày lùi (mặc định 90)
  MIN_IMPR         ngưỡng impressions tối thiểu của URL thứ 2 để coi là cắn nhau (mặc định 10)
"""
import os, json, datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_raw = os.environ["SITE_URL"].strip()
SITE_URL = _raw if _raw.startswith("sc-domain:") else _raw.rstrip("/") + "/"
SHEET_ID = os.environ.get("CANNIBAL_SHEET_ID", os.environ.get("SHEET_ID", "")).strip()
TAB = os.environ.get("CANNIBAL_TAB", "GSC Ăn Thịt")
DAYS = int(os.environ.get("DAYS", "90"))
MIN_IMPR = int(os.environ.get("MIN_IMPR", "10"))


def creds():
    info = json.loads(os.environ["GCP_SA_JSON"])
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def fetch_rows(sc):
    """Kéo toàn bộ (query, page) 90 ngày, phân trang."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=DAYS)
    rows, start_row = [], 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": 25000,
            "startRow": start_row,
            "dataState": "final",
        }
        resp = sc.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < 25000:
            break
        start_row += 25000
    return rows


def is_product(u):
    return "/san-pham/" in u


def is_post(u):
    return u.rstrip("/").count("/") == 3 and "/san-pham/" not in u and "/danh-muc" not in u \
        and "/category/" not in u and "/page/" not in u


def analyze(rows):
    byq = {}
    for r in rows:
        q, page = r["keys"][0], r["keys"][1]
        byq.setdefault(q, []).append({
            "page": page, "clicks": r.get("clicks", 0),
            "impr": r.get("impressions", 0), "pos": r.get("position", 0),
        })
    out = []
    for q, pages in byq.items():
        pages = [p for p in pages if p["impr"] > 0]
        if len(pages) < 2:
            continue
        pages.sort(key=lambda p: -p["impr"])
        second = pages[1]
        if second["impr"] < MIN_IMPR:
            continue  # URL thứ 2 quá ít hiển thị → không tính là cắn nhau
        urls = [p["page"] for p in pages]
        kinds = set()
        for u in urls[:3]:
            if is_product(u): kinds.add("SP")
            elif is_post(u): kinds.add("Bài")
            else: kinds.add("Khác")
        danger = "🔴 Bài↔SP" if {"SP", "Bài"} <= kinds else ("🟠 cùng loại" if len(kinds) == 1 else "🟡 hỗn hợp")
        total_impr = sum(p["impr"] for p in pages)
        total_clicks = sum(p["clicks"] for p in pages)
        out.append({
            "q": q, "nurls": len(pages), "danger": danger,
            "impr": total_impr, "clicks": total_clicks,
            "u1": pages[0]["page"], "i1": pages[0]["impr"], "p1": round(pages[0]["pos"], 1),
            "u2": pages[1]["page"], "i2": pages[1]["impr"], "p2": round(pages[1]["pos"], 1),
        })
    out.sort(key=lambda x: -x["impr"])
    return out


def write_sheet(sh, data):
    # tạo tab nếu chưa có
    meta = sh.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta["sheets"]]
    if TAB not in tabs:
        sh.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": TAB}}}]},
        ).execute()
    today = datetime.date.today().isoformat()
    header = [
        [f"GSC XÁC NHẬN ĂN THỊT TỪ KHÓA — {SITE_URL} — {DAYS} ngày — cập nhật {today}"],
        ["Truy vấn mà Google cho ≥2 URL cùng nhận hiển thị = đang cắn nhau thật. Sắp theo tổng impressions."],
        [],
        ["Truy vấn", "Số URL", "Mức độ", "Tổng Impr", "Tổng Click",
         "URL #1", "Impr#1", "Vị trí#1", "URL #2", "Impr#2", "Vị trí#2"],
    ]
    body_rows = [[
        d["q"], d["nurls"], d["danger"], d["impr"], d["clicks"],
        d["u1"].replace(SITE_URL, "/"), d["i1"], d["p1"],
        d["u2"].replace(SITE_URL, "/"), d["i2"], d["p2"],
    ] for d in data]
    values = header + body_rows
    sh.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"'{TAB}'!A1",
        valueInputOption="RAW", body={"values": values},
    ).execute()
    return len(body_rows)


def main():
    c = creds()
    sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
    sh = build("sheets", "v4", credentials=c, cache_discovery=False)
    print(f"Kéo GSC {DAYS} ngày cho {SITE_URL} ...")
    rows = fetch_rows(sc)
    print(f"  {len(rows)} dòng (query,page)")
    data = analyze(rows)
    n_danger = sum(1 for d in data if d["danger"].startswith("🔴"))
    print(f"  Truy vấn cắn nhau: {len(data)} (trong đó Bài↔SP: {n_danger})")
    if not SHEET_ID:
        print("!! Thiếu CANNIBAL_SHEET_ID/SHEET_ID — chỉ in, không ghi sheet.")
        for d in data[:30]:
            print(f"  [{d['danger']}] {d['q']}  ({d['nurls']} URL, {d['impr']} impr)")
        return
    n = write_sheet(sh, data)
    print(f"Đã ghi {n} dòng vào tab '{TAB}'.")


if __name__ == "__main__":
    main()

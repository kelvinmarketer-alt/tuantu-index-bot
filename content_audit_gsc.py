#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
content_audit_gsc.py — Lọc bài/trang CẦN AUDIT theo dữ liệu GSC (không mò).

Bucket mỗi URL theo vị trí TB + CTR (90 ngày):
  🔥 Quick win   : vị trí 10.5–20.5  → tinh chỉnh nhẹ đẩy lên trang 1 (ROI cao nhất)
  🎯 Cận kề      : vị trí 20.5–40.5  → bổ sung ý + internal link
  ✍️ CTR thấp    : vị trí ≤10.5 nhưng CTR thấp → chỉ viết lại title/meta
  ✅ Tốt          : trang 1, CTR ổn → để yên
  👻 Ít hiển thị : impressions rất thấp → cần index/authority/refresh
Cộng: đối chiếu sitemap → đếm URL có 0 hiển thị (không xuất hiện trên GSC).

Chạy bằng service account bot index (webmasters.readonly). Ghi tab 'GSC Audit Bài'.
ENV: GCP_SA_JSON, SITE_URL, CANNIBAL_SHEET_ID/SHEET_ID, AUDIT_TAB(=GSC Audit Bài), DAYS(=90)
"""
import os, json, time, datetime, ssl, urllib.request, re
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]
_raw = os.environ["SITE_URL"].strip()
SITE_URL = _raw if _raw.startswith("sc-domain:") else _raw.rstrip("/") + "/"
HOST = "https://nongsantuantuhanoi.vn"
SHEET_ID = os.environ.get("CANNIBAL_SHEET_ID", os.environ.get("SHEET_ID", "")).strip()
TAB = os.environ.get("AUDIT_TAB", "GSC Audit Bài")
DAYS = int(os.environ.get("DAYS", "90"))


def _retry(fn, tries=4, base=2.0):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(base * (i + 1))


def creds():
    return service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GCP_SA_JSON"]), scopes=SCOPES)


def fetch_pages(sc):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=DAYS)
    rows, sr = [], 0
    while True:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["page"], "rowLimit": 25000, "startRow": sr, "dataState": "final"}
        resp = _retry(lambda: sc.searchanalytics().query(siteUrl=SITE_URL, body=body).execute())
        b = resp.get("rows", [])
        rows.extend(b)
        if len(b) < 25000:
            break
        sr += 25000
    return rows


def all_sitemap_urls():
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    urls = set()
    try:
        idx = urllib.request.urlopen(f"{HOST}/sitemap_index.xml", timeout=25, context=ctx).read().decode("utf-8", "ignore")
        subs = re.findall(r"<loc>([^<]+)</loc>", idx)
        for s in subs:
            try:
                x = urllib.request.urlopen(s, timeout=25, context=ctx).read().decode("utf-8", "ignore")
                urls.update(re.findall(r"<loc>([^<]+)</loc>", x))
            except Exception:
                pass
    except Exception:
        pass
    return urls


def ptype(u):
    if "/san-pham/" in u:
        return "SP"
    if "/danh-muc-san-pham/" in u or "/product_cat" in u:
        return "Danh mục SP"
    if "/category/" in u:
        return "Chuyên mục"
    if u.rstrip("/") == HOST:
        return "Trang chủ"
    if u.rstrip("/").count("/") == 3:
        return "Bài viết"
    return "Trang"


def bucket(pos, ctr, impr):
    if impr < 8:
        return ("👻 Ít hiển thị", "Cần index/authority hoặc refresh — chưa được thấy")
    if 10.5 < pos <= 20.5:
        return ("🔥 Quick win (11–20)", "Tinh chỉnh nhẹ + internal link → đẩy lên trang 1")
    if 20.5 < pos <= 40.5:
        return ("🎯 Cận kề (21–40)", "Bổ sung ý, thêm H2/FAQ, internal link")
    if pos <= 10.5 and ctr < 0.015 and impr >= 30:
        return ("✍️ CTR thấp (top nhưng ít click)", "Viết lại title/meta hấp dẫn hơn — KHÔNG đụng nội dung")
    if pos <= 10.5:
        return ("✅ Tốt (trang 1)", "Để yên / refresh định kỳ")
    return ("⚪ Khác (>40)", "Xa top — ưu tiên sau")


def main():
    c = creds()
    sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
    sh = build("sheets", "v4", credentials=c, cache_discovery=False)
    print(f"GSC pages {DAYS} ngày cho {SITE_URL} ...")
    rows = fetch_pages(sc)
    print(f"  {len(rows)} URL có hiển thị")

    data = []
    seen = set()
    for r in rows:
        u = r["keys"][0]; seen.add(u.rstrip("/") + "/")
        impr = r.get("impressions", 0); clicks = r.get("clicks", 0)
        ctr = r.get("ctr", 0); pos = r.get("position", 0)
        bk, tip = bucket(pos, ctr, impr)
        data.append({"u": u, "type": ptype(u), "impr": impr, "clicks": clicks,
                     "ctr": round(ctr * 100, 1), "pos": round(pos, 1), "bk": bk, "tip": tip})

    # sitemap diff → URL 0 hiển thị
    sm = all_sitemap_urls()
    sm_norm = {u.rstrip("/") + "/" for u in sm}
    zero = sorted(u for u in sm_norm if u not in seen)
    zero_posts = [u for u in zero if ptype(u) == "Bài viết"]
    print(f"  Sitemap {len(sm_norm)} URL | 0 hiển thị: {len(zero)} (bài: {len(zero_posts)})")

    # thứ tự bucket ưu tiên
    order = {"🔥 Quick win (11–20)": 0, "🎯 Cận kề (21–40)": 1, "✍️ CTR thấp (top nhưng ít click)": 2,
             "👻 Ít hiển thị": 3, "✅ Tốt (trang 1)": 4, "⚪ Khác (>40)": 5}
    data.sort(key=lambda d: (order.get(d["bk"], 9), -d["impr"]))
    counts = {}
    for d in data:
        counts[d["bk"]] = counts.get(d["bk"], 0) + 1

    today = datetime.date.today().isoformat()
    hdr = [[f"GSC AUDIT BÀI/TRANG — {SITE_URL} — {DAYS} ngày — {today}"],
           ["Bucket theo vị trí TB + CTR. Ưu tiên từ trên xuống: 🔥 → 🎯 → ✍️ → 👻."],
           ["TÓM TẮT: " + " | ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: order.get(x[0], 9)))],
           [f"URL trong sitemap CÓ 0 hiển thị GSC: {len(zero)} (trong đó bài viết: {len(zero_posts)}) → nút thắt index/authority"],
           [],
           ["URL", "Loại", "Impr", "Click", "CTR %", "Vị trí TB", "Ô cần làm", "Gợi ý"]]
    body = [[d["u"].replace(HOST, ""), d["type"], d["impr"], d["clicks"], d["ctr"], d["pos"], d["bk"], d["tip"]]
            for d in data]
    # thêm mục 0-hiển thị (bài) ở cuối, tối đa 60 dòng
    if zero_posts:
        body.append([])
        body.append(["— BÀI 0 HIỂN THỊ (cần ép index / bồi authority) —", "", "", "", "", "", "", ""])
        for u in zero_posts[:60]:
            body.append([u.replace(HOST, ""), "Bài viết", 0, 0, 0, "", "👻 0 hiển thị", "Ép index + internal link từ bài top"])

    values = hdr + body
    meta = _retry(lambda: sh.spreadsheets().get(spreadsheetId=SHEET_ID).execute())
    if TAB not in [s["properties"]["title"] for s in meta["sheets"]]:
        _retry(lambda: sh.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID, body={"requests": [{"addSheet": {"properties": {"title": TAB}}}]}).execute())
    _retry(lambda: sh.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"'{TAB}'!A1", valueInputOption="RAW", body={"values": values}).execute())
    print(f"Đã ghi {len(body)} dòng vào '{TAB}'. Tóm tắt bucket: {counts}")
    # Dump JSON đầy đủ các ô CẦN LÀM để pipeline ngoài đọc lại.
    want = {"🔥 Quick win (11–20)": "quickwin", "🎯 Cận kề (21–40)": "canke",
            "✍️ CTR thấp (top nhưng ít click)": "ctrlow"}
    dump = {v: [] for v in want.values()}
    for d in data:
        if d["bk"] in want:
            dump[want[d["bk"]]].append([d["u"].replace(HOST, ""), d["pos"], d["ctr"], d["impr"], d["type"]])
    print("AUDITJSON_START")
    print(json.dumps(dump, ensure_ascii=False))
    print("AUDITJSON_END")


if __name__ == "__main__":
    main()

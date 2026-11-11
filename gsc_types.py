#!/usr/bin/env python3
"""Tóm tắt GSC 28 ngày theo LOẠI TRANG + top trang mỗi loại + thiết bị/quốc gia. In text thường (không JSON). Chỉ đọc."""
import os, json, datetime, re, urllib.request, ssl
from collections import defaultdict
from google.oauth2 import service_account
from googleapiclient.discovery import build
raw = os.environ["SITE_URL"].strip(); SITE = raw if raw.startswith("sc-domain:") else raw.rstrip("/") + "/"
HOST = "https://nongsantuantuhanoi.vn"
c = service_account.Credentials.from_service_account_info(json.loads(os.environ["GCP_SA_JSON"]), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
end = datetime.date.today() - datetime.timedelta(days=3); s28 = end - datetime.timedelta(days=27)
def q(dims, a=s28, b=end):
    out, sr = [], 0
    while True:
        r = sc.searchanalytics().query(siteUrl=SITE, body={"startDate": a.isoformat(), "endDate": b.isoformat(), "dimensions": dims, "rowLimit": 25000, "startRow": sr}).execute().get("rows", [])
        out += r
        if len(r) < 25000: return out
        sr += 25000
def typ(u):
    p = u.replace(HOST, "").replace("http://nongsantuantuhanoi.vn", "")
    if p in ("", "/"): return "home"
    if p.startswith("/san-pham/"): return "product"
    if p.startswith("/danh-muc-san-pham/"): return "product_cat"
    if p.startswith("/category/"): return "blog_cat"
    if p.startswith("/cua-hang"): return "shop"
    if p.strip("/") in ("bang-gia","lien-he","gioi-thieu","nong-san-tuan-tu-ha-noi","bao-chi","he-thong-phan-phoi","tuyen-dung","doi-ngu","tac-gia","tin-tuc","huong-dan-mua-hang","chinh-sach-van-chuyen","chinh-sach-doi-tra","chinh-sach-thanh-toan","bao-mat-thong-tin","dieu-khoan-dich-vu","tin-tuc-noi-bo","ung-tuyen","nguyen-lieu-nha-hang-chay","thuc-pham-bep-an-cong-nghiep","nguyen-lieu-quan-nuong-bbq","nguyen-lieu-quan-lau"): return "page:" + p.strip("/")
    if p.count("/") == 2: return "post"
    return "other"
rows = q(["page"])
agg = defaultdict(lambda: [0, 0, 0.0, 0])
for r in rows:
    t = typ(r["keys"][0]); t2 = "page" if t.startswith("page:") else t
    for k in {t, t2}:
        a = agg[k]; a[0] += r["clicks"]; a[1] += r["impressions"]; a[2] += r["position"] * r["impressions"]; a[3] += 1
print(f"KỲ\t{s28}\t{end}")
for k, a in sorted(agg.items(), key=lambda x: -x[1][1]):
    print(f"TYPE\t{k}\t{a[3]}\t{a[0]}\t{a[1]}\t{round(a[0]/a[1]*100,2) if a[1] else 0}\t{round(a[2]/a[1],1) if a[1] else 0}")
for t in ("product", "post", "product_cat"):
    top = sorted([r for r in rows if typ(r["keys"][0]) == t], key=lambda r: -r["clicks"])[:8]
    for r in top: print(f"TOP\t{t}\t{r['keys'][0].replace(HOST,'')}\t{r['clicks']}\t{r['impressions']}\t{round(r['position'],1)}")
for r in q(["device"]): print(f"DEV\t{r['keys'][0]}\t{r['clicks']}\t{r['impressions']}\t{round(r['ctr']*100,2)}\t{round(r['position'],1)}")
qs = q(["query"]); br = [r for r in qs if re.search(r"tu[aâấ]n t[uú]|tuantu", r["keys"][0])]
print(f"QUERY\ttổng\t{len(qs)}\t{sum(r['clicks'] for r in qs)}\t{sum(r['impressions'] for r in qs)}")
print(f"QUERY\tthương hiệu\t{len(br)}\t{sum(r['clicks'] for r in br)}\t{sum(r['impressions'] for r in br)}")
pos = defaultdict(int)
for r in qs:
    p = r["position"]; pos["1-3" if p<=3 else "4-10" if p<=10 else "11-20" if p<=20 else ">20"] += r["impressions"]
for k in ("1-3","4-10","11-20",">20"): print(f"POS\t{k}\t{pos[k]}")
for r in q(["searchAppearance"]): print(f"APP\t{r['keys'][0]}\t{r['clicks']}\t{r['impressions']}")

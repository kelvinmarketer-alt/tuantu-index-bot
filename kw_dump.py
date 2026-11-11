#!/usr/bin/env python3
"""kw_dump.py — CHỈ ĐỌC GSC: xuất truy vấn×trang 3 kỳ 28 ngày (P1 mới nhất, P0 trước đó, P2 cách 2 kỳ)
+ 90 ngày + theo ngày. Repo public → file được mã hoá bằng khoá công khai dump_pub.pem (khoá riêng chỉ ở máy).
ENV: GCP_SA_JSON, SITE_URL, OUT."""
import os, json, datetime, time
from google.oauth2 import service_account
from googleapiclient.discovery import build
raw = os.environ["SITE_URL"].strip()
SITE = raw if raw.startswith("sc-domain:") else raw.rstrip("/") + "/"
c = service_account.Credentials.from_service_account_info(json.loads(os.environ["GCP_SA_JSON"]),
    scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
D = datetime.timedelta
end = datetime.date.today() - D(days=3)
def q(s, e, dims):
    rows, sr = [], 0
    while True:
        body = {"startDate": s.isoformat(), "endDate": e.isoformat(), "dimensions": dims, "rowLimit": 25000, "startRow": sr}
        for i in range(4):
            try:
                b = sc.searchanalytics().query(siteUrl=SITE, body=body).execute().get("rows", []); break
            except Exception:
                if i == 3: raise
                time.sleep(3 * (i + 1))
        rows += [[*r["keys"], r["clicks"], r["impressions"], round(r["position"], 2)] for r in b]
        if len(b) < 25000: return rows
        sr += 25000
out = {"end": end.isoformat()}
for name, k in (("P1", 0), ("P0", 1), ("P2", 2)):
    e = end - D(days=28 * k); s = e - D(days=27)
    out[name] = {"range": [s.isoformat(), e.isoformat()], "qp": q(s, e, ["query", "page"])}
s90 = end - D(days=89)
out["Q90"] = {"range": [s90.isoformat(), end.isoformat()], "qp": q(s90, end, ["query", "page"])}
out["W"] = q(end - D(days=111), end, ["date"])
json.dump(out, open(os.environ.get("OUT", "kw.json"), "w"), ensure_ascii=False)
print({k: len(v["qp"]) for k, v in out.items() if isinstance(v, dict)})

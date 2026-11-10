#!/usr/bin/env python3
"""In truy vấn GSC 90 ngày của các trang chỉ định (PAGES = danh sách path, cách nhau dấu phẩy). Chỉ đọc."""
import os, json, datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
raw = os.environ["SITE_URL"].strip()
SITE = raw if raw.startswith("sc-domain:") else raw.rstrip("/") + "/"
HOST = "https://nongsantuantuhanoi.vn"
c = service_account.Credentials.from_service_account_info(json.loads(os.environ["GCP_SA_JSON"]),
    scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
end = datetime.date.today() - datetime.timedelta(days=3); start = end - datetime.timedelta(days=89)
for p in [x.strip() for x in os.environ["PAGES"].split(",") if x.strip()]:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["query"], "rowLimit": 60,
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": HOST + p}]}]}
    rows = sc.searchanalytics().query(siteUrl=SITE, body=body).execute().get("rows", [])
    rows.sort(key=lambda r: -r["impressions"])
    print(f"PAGE\t{p}\t{len(rows)}")
    for r in rows:
        print(f"Q\t{p}\t{r['keys'][0]}\t{r['impressions']}\t{r['clicks']}\t{round(r['position'],1)}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seo_deep.py — Rà SEO sâu toàn site (CHỈ ĐỌC GSC + đọc HTML công khai), ghi 4 tab vào sheet bot.

  Deep·Tổng   : tổng click/hiển thị theo tuần (16 tuần) + 28 ngày vs 28 ngày trước
  Deep·Trang  : mỗi URL — 28d vs 28d trước (impr/click/vị trí) + từ khoá chính 90 ngày
  Deep·Query  : top truy vấn 28 ngày (trang nhận, vị trí, so kỳ trước)
  Deep·Onpage : quét HTML bài/trang/danh mục + SP có hiển thị — title/meta/H1/số chữ/link/ảnh/canonical/robots
                + kiểm link nội bộ hỏng/redirect (1 luồng, giãn nhịp)

ENV: GCP_SA_JSON, SITE_URL, SHEET_ID, CRAWL_DELAY(=0.6), LINK_CAP(=1500)
"""
import os, json, time, datetime, re, html
from collections import defaultdict
from urllib.parse import urljoin, urlparse
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]
_raw = os.environ["SITE_URL"].strip()
SITE_URL = _raw if _raw.startswith("sc-domain:") else _raw.rstrip("/") + "/"
HOST = "https://nongsantuantuhanoi.vn"
SHEET_ID = os.environ["SHEET_ID"].strip()
DELAY = float(os.environ.get("CRAWL_DELAY", "0.6"))
LINK_CAP = int(os.environ.get("LINK_CAP", "1500"))
UA = {"User-Agent": "Mozilla/5.0 (compatible; NSTT-SEO-audit/1.0)"}
S = requests.Session(); S.headers.update(UA)


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


def q(sc, start, end, dims, limit=25000):
    rows, sr = [], 0
    while True:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dims,
                "rowLimit": limit, "startRow": sr}
        b = _retry(lambda: sc.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()).get("rows", [])
        rows.extend(b)
        if len(b) < limit:
            return rows
        sr += limit


def norm(u):
    return u.split("#")[0].rstrip("/") + "/" if u else u


def short(u):
    return u.replace(HOST, "") or "/"


def write_tab(sh, tab, values):
    meta = _retry(lambda: sh.spreadsheets().get(spreadsheetId=SHEET_ID).execute())
    names = [s["properties"]["title"] for s in meta["sheets"]]
    if tab not in names:
        _retry(lambda: sh.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={
            "requests": [{"addSheet": {"properties": {"title": tab, "gridProperties": {"rowCount": max(1000, len(values) + 50), "columnCount": 26}}}}]}).execute())
    else:
        _retry(lambda: sh.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range=f"'{tab}'").execute())
    for i in range(0, len(values), 300):
        chunk = values[i:i + 300]
        _retry(lambda: sh.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"'{tab}'!A{i + 1}", valueInputOption="RAW",
            body={"values": chunk}).execute())
    print(f"  ghi {len(values)} dòng → {tab}")


def sitemap_urls():
    out = {}
    idx = S.get(f"{HOST}/sitemap_index.xml", timeout=30).text
    for sm in re.findall(r"<loc>([^<]+)</loc>", idx):
        kind = re.sub(r"\d*\.xml$", "", sm.rsplit("/", 1)[-1]).replace("-sitemap", "")
        try:
            x = S.get(sm, timeout=30).text
            for u in re.findall(r"<loc>([^<]+)</loc>", x):
                out[norm(u)] = kind
        except Exception:
            pass
        time.sleep(DELAY)
    return out


def text_of(h):
    h = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", h)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", h))


def onpage(u):
    r = S.get(u, timeout=40, allow_redirects=True)
    h = r.text
    final = norm(r.url)
    title = html.unescape((re.search(r"(?is)<title[^>]*>(.*?)</title>", h) or [None, ""])[1]).strip()
    desc = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', h)
    desc = html.unescape(desc.group(1)).strip() if desc else ""
    robots = re.search(r'(?is)<meta[^>]+name=["\']robots["\'][^>]+content=["\']([^"\']*)', h)
    robots = robots.group(1) if robots else ""
    canon = re.search(r'(?is)<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']*)', h)
    canon = norm(canon.group(1)) if canon else ""
    h1s = [text_of(x).strip() for x in re.findall(r"(?is)<h1[^>]*>(.*?)</h1>", h)]
    # Vùng nội dung chính: <article>/<main> nếu có
    m = re.search(r"(?is)<article[^>]*>(.*)</article>", h) or re.search(r"(?is)<main[^>]*>(.*)</main>", h)
    body = m.group(1) if m else h
    words = len(re.findall(r"\w+", text_of(body)))
    h2 = len(re.findall(r"(?i)<h2[\s>]", body))
    links = re.findall(r'(?is)<a\s[^>]*href=["\']([^"\'#]+)', body)
    internal, external = set(), 0
    for l in links:
        a = urljoin(u, l)
        p = urlparse(a)
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc.endswith("nongsantuantuhanoi.vn"):
            if any(x in p.path for x in ("/wp-", "/cart", "/gio-hang", "/checkout", "/thanh-toan", "/tai-khoan", "/my-account")) or p.query:
                continue
            internal.add(norm(f"{p.scheme}://{p.netloc}{p.path}"))
        else:
            external += 1
    imgs = re.findall(r"(?is)<img\s[^>]*>", body)
    noalt = sum(1 for i in imgs if not re.search(r'alt=["\'][^"\']+["\']', i))
    schema = sorted(set(re.findall(r'"@type"\s*:\s*"([A-Za-z]+)"', h)))
    faq = "FAQPage" in schema
    return {"status": r.status_code, "final": final, "title": title, "desc": desc, "robots": robots,
            "canon": canon, "h1": h1s, "words": words, "h2": h2, "internal": internal, "ext": external,
            "imgs": len(imgs), "noalt": noalt, "schema": ",".join(schema)[:120], "faq": faq,
            "redirected": norm(u) != final}


def main():
    c = creds()
    sc = build("searchconsole", "v1", credentials=c, cache_discovery=False)
    sh = build("sheets", "v4", credentials=c, cache_discovery=False)
    end = datetime.date.today() - datetime.timedelta(days=3)       # GSC trễ ~2-3 ngày
    s28 = end - datetime.timedelta(days=27)
    p_end = s28 - datetime.timedelta(days=1)
    p_s28 = p_end - datetime.timedelta(days=27)
    s90 = end - datetime.timedelta(days=89)
    today = datetime.date.today().isoformat()
    lbl = f"28d {s28}→{end} so với {p_s28}→{p_end}"
    print("GSC:", lbl)

    # ---------- Tổng theo tuần ----------
    daily = q(sc, end - datetime.timedelta(days=16 * 7 - 1), end, ["date"])
    wk = defaultdict(lambda: [0, 0, 0.0])
    for r in daily:
        d = datetime.date.fromisoformat(r["keys"][0])
        k = (d - datetime.timedelta(days=d.weekday())).isoformat()
        wk[k][0] += r["clicks"]; wk[k][1] += r["impressions"]; wk[k][2] += r["position"] * r["impressions"]
    tot = [[f"TỔNG GSC — {SITE_URL} — chạy {today}"], [], ["Tuần (thứ 2)", "Click", "Hiển thị", "CTR %", "Vị trí TB"]]
    for k in sorted(wk):
        cl, im, pw = wk[k]
        tot.append([k, cl, im, round(cl / im * 100, 2) if im else 0, round(pw / im, 1) if im else 0])

    def totals(a, b):
        rr = q(sc, a, b, [])
        if not rr:
            return [0, 0, 0, 0]
        r = rr[0]
        return [r["clicks"], r["impressions"], round(r["ctr"] * 100, 2), round(r["position"], 1)]
    t1, t0 = totals(s28, end), totals(p_s28, p_end)
    tot += [[], [lbl], ["", "Click", "Hiển thị", "CTR %", "Vị trí TB"], ["28 ngày gần nhất"] + t1, ["28 ngày trước"] + t0]
    # theo thiết bị + quốc gia VN
    dev = q(sc, s28, end, ["device"])
    tot += [[], ["Thiết bị (28d)", "Click", "Hiển thị", "CTR %", "Vị trí TB"]]
    for r in dev:
        tot.append([r["keys"][0], r["clicks"], r["impressions"], round(r["ctr"] * 100, 2), round(r["position"], 1)])
    write_tab(sh, "Deep·Tổng", tot)

    # ---------- Trang ----------
    def bypage(a, b):
        return {norm(r["keys"][0]): r for r in q(sc, a, b, ["page"])}
    P1, P0 = bypage(s28, end), bypage(p_s28, p_end)
    pq = q(sc, s90, end, ["page", "query"])
    topq = defaultdict(list)
    for r in pq:
        topq[norm(r["keys"][0])].append(r)
    for k in topq:
        topq[k].sort(key=lambda r: -r["impressions"])
    sm = sitemap_urls()
    print(f"  sitemap {len(sm)} URL | trang có hiển thị 28d: {len(P1)}")
    pages = set(P1) | set(P0)
    rows = []
    for u in pages:
        a, b = P1.get(u, {}), P0.get(u, {})
        tq = topq.get(u, [])
        t = tq[0] if tq else {}
        rows.append([short(u), sm.get(u, "—ngoài sitemap"),
                     a.get("impressions", 0), b.get("impressions", 0), a.get("impressions", 0) - b.get("impressions", 0),
                     a.get("clicks", 0), b.get("clicks", 0),
                     round(a["position"], 1) if a else "", round(b["position"], 1) if b else "",
                     round(a.get("ctr", 0) * 100, 2),
                     t["keys"][1] if t else "", t.get("impressions", 0), round(t["position"], 1) if t else "",
                     len(tq), " | ".join(f'{x["keys"][1]} ({x["impressions"]}, #{round(x["position"],1)})' for x in tq[1:5])])
    rows.sort(key=lambda r: -(r[2] + r[3]))
    write_tab(sh, "Deep·Trang", [[f"TRANG — {lbl} — từ khoá 90 ngày"], [],
                                 ["URL", "Loại", "Impr 28d", "Impr trước", "Δ Impr", "Click 28d", "Click trước",
                                  "Vị trí 28d", "Vị trí trước", "CTR % 28d", "Từ khoá chính (90d)", "Impr KW", "Vị trí KW",
                                  "Số KW", "KW phụ"]] + rows)

    # ---------- Query ----------
    Q1 = q(sc, s28, end, ["query", "page"])
    Q0 = {(r["keys"][0], norm(r["keys"][1])): r for r in q(sc, p_s28, p_end, ["query", "page"])}
    qn = defaultdict(set)
    for r in Q1:
        qn[r["keys"][0]].add(norm(r["keys"][1]))
    Q1.sort(key=lambda r: -r["impressions"])
    qrows = []
    for r in Q1[:600]:
        k = (r["keys"][0], norm(r["keys"][1]))
        b = Q0.get(k)
        qrows.append([r["keys"][0], short(norm(r["keys"][1])), r["impressions"], r["clicks"], round(r["ctr"] * 100, 2),
                      round(r["position"], 1), round(b["position"], 1) if b else "", b["impressions"] if b else 0,
                      len(qn[r["keys"][0]])])
    write_tab(sh, "Deep·Query", [[f"TRUY VẤN × TRANG — {lbl} (top 600 theo hiển thị)"], [],
                                 ["Truy vấn", "Trang", "Impr", "Click", "CTR %", "Vị trí", "Vị trí trước", "Impr trước",
                                  "Số trang cùng ăn query"]] + qrows)

    # ---------- On-page ----------
    targets = [u for u, k in sm.items() if k in ("post", "page", "category", "product_cat")]
    prod_hot = sorted([u for u in P1 if sm.get(u) == "product"], key=lambda u: -P1[u]["impressions"])[:150]
    targets += prod_hot
    targets.append(norm(HOST))
    targets = list(dict.fromkeys(targets))
    print(f"  quét on-page {len(targets)} URL ...")
    res, inlinks = {}, defaultdict(set)
    for i, u in enumerate(targets):
        try:
            res[u] = onpage(u)
            for l in res[u]["internal"]:
                if l != u:
                    inlinks[l].add(u)
        except Exception as e:
            res[u] = {"err": str(e)[:80]}
        time.sleep(DELAY)
        if i % 50 == 0:
            print(f"    {i}/{len(targets)}")
    # Kiểm link nội bộ được trỏ tới
    all_links = set()
    for v in res.values():
        all_links |= v.get("internal", set())
    check = [l for l in all_links if l not in res][:LINK_CAP]
    print(f"  kiểm {len(check)} link nội bộ ...")
    lstat = {}
    for l in check:
        try:
            r = S.head(l, timeout=25, allow_redirects=False)
            if r.status_code == 405:
                r = S.get(l, timeout=25, allow_redirects=False)
            lstat[l] = (r.status_code, r.headers.get("Location", ""))
        except Exception as e:
            lstat[l] = ("ERR", str(e)[:60])
        time.sleep(DELAY / 2)
    for u, v in res.items():
        if "status" in v:
            lstat[u] = (v["status"] if not v["redirected"] else 301, v["final"] if v["redirected"] else "")

    orow = []
    for u in targets:
        v = res[u]
        if "err" in v:
            orow.append([short(u), sm.get(u, ""), "ERR " + v["err"]]); continue
        bad = [l for l in v["internal"] if lstat.get(l, (200,))[0] not in (200,)]
        issues = []
        tl = len(v["title"])
        if tl > 65: issues.append("title dài")
        if tl < 30: issues.append("title ngắn")
        dl = len(v["desc"])
        if dl == 0: issues.append("thiếu meta desc")
        elif dl > 165: issues.append("desc dài")
        elif dl < 90: issues.append("desc ngắn")
        if len(v["h1"]) != 1: issues.append(f"{len(v['h1'])} H1")
        if v["canon"] and v["canon"] != v["final"]: issues.append("canonical khác")
        if "noindex" in v["robots"].lower(): issues.append("noindex")
        if v["redirected"]: issues.append("URL sitemap bị redirect")
        if bad: issues.append(f"{len(bad)} link hỏng/redirect")
        if v["noalt"]: issues.append(f"{v['noalt']} ảnh thiếu alt")
        if sm.get(u) == "post" and v["words"] < 1200: issues.append("bài <1200 chữ")
        if sm.get(u) == "post" and len(inlinks.get(u, ())) == 0: issues.append("0 link trỏ về (trong mẫu quét)")
        g = P1.get(u, {})
        orow.append([short(u), sm.get(u, "—"), v["status"], v["title"], tl, dl, len(v["h1"]), (v["h1"][0] if v["h1"] else "")[:90],
                     v["words"], v["h2"], len(v["internal"]), len(inlinks.get(u, ())), v["ext"], v["imgs"], v["noalt"],
                     "có" if v["faq"] else "", v["schema"], v["robots"], "" if not v["canon"] or v["canon"] == v["final"] else short(v["canon"]),
                     g.get("impressions", 0), g.get("clicks", 0), round(g["position"], 1) if g else "",
                     "; ".join(issues), " ".join(short(b) + f"[{lstat[b][0]}]" for b in bad[:6])])
    broken = sorted({(short(l), str(s[0]), short(s[1]) if s[1] else "", len(inlinks.get(l, ())))
                     for l, s in lstat.items() if s[0] not in (200,)}, key=lambda x: -x[3])
    out = [[f"ON-PAGE — quét {len(targets)} URL (bài/trang/danh mục + {len(prod_hot)} SP có hiển thị) — {today}"], [],
           ["URL", "Loại", "HTTP", "Title", "Dài title", "Dài desc", "Số H1", "H1", "Số chữ", "Số H2", "Link nội bộ ra",
            "Link trỏ về (mẫu)", "Link ngoài", "Ảnh", "Ảnh thiếu alt", "FAQ", "Schema", "Robots", "Canonical khác",
            "Impr 28d", "Click 28d", "Vị trí 28d", "VẤN ĐỀ", "Link hỏng trong bài"]] + orow
    out += [[], ["— LINK NỘI BỘ KHÔNG TRẢ 200 (đang được trỏ tới) —"], ["Link", "HTTP", "Chuyển tới", "Số trang trỏ tới"]]
    out += [list(b) for b in broken[:400]]
    write_tab(sh, "Deep·Onpage", out)
    print("XONG.")


if __name__ == "__main__":
    main()

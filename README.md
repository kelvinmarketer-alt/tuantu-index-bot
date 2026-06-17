# tuantu-index-bot

Bot tự động **kiểm tra index + ép index nhanh** cho website `nongsantuantuhanoi.vn`, báo cáo vào Google Sheet và Telegram mỗi sáng 07:00 (giờ VN).

## Bot làm gì mỗi ngày
1. Đọc **sitemap** → lấy danh sách URL (tự động, không cần khai tay).
2. Gọi **Google Search Console URL Inspection API** → biết URL nào đã/chưa index.
3. Ghi báo cáo (theo ngày) vào **Google Sheet**.
4. URL **chưa index** → tự **submit qua Indexing API** để ép index nhanh.
5. Gửi **tóm tắt vào Telegram**.

> ⚠️ Index report qua GSC **chỉ dùng cho web mình sở hữu & đã verify** (nongsantuantuhanoi.vn).
> Link social (blogspot, wordpress.com, telegra.ph…) KHÔNG inspect được qua đây — dùng dịch vụ ép index riêng.

---

## Tài nguyên đã tạo sẵn
- **Google Sheet báo cáo:** `1DMDPArnJKwz2qRHtDMDBG7kT0Clgm4UBq7PxaIvE6Tk`
  → https://docs.google.com/spreadsheets/d/1DMDPArnJKwz2qRHtDMDBG7kT0Clgm4UBq7PxaIvE6Tk/edit
  (Tab dữ liệu `Index Report` sẽ được bot tự tạo lần chạy đầu.)

---

## Cần làm 1 LẦN (setup)

### 1. Google Cloud — bật API + tạo Service Account
1. Tạo (hoặc dùng) 1 project tại https://console.cloud.google.com
2. **APIs & Services → Enable APIs**, bật 3 API:
   - **Google Search Console API**
   - **Web Search Indexing API** (Indexing API)
   - **Google Sheets API**
3. **IAM & Admin → Service Accounts → Create**:
   - Tạo service account, ví dụ `index-bot`.
   - Vào service account → **Keys → Add key → JSON** → tải file `sa.json`.
   - Ghi nhớ **email** của service account (dạng `index-bot@<project>.iam.gserviceaccount.com`).

### 2. Cấp quyền cho Service Account
- **Search Console** (https://search.google.com/search-console):
  chọn property `nongsantuantuhanoi.vn` → **Settings → Users and permissions → Add user**
  → dán email service account → quyền **Owner** (Indexing API bắt buộc Owner).
- **Google Sheet báo cáo**: bấm Share → thêm email service account → quyền **Editor**.

### 3. Telegram
- Tạo bot qua [@BotFather](https://t.me/BotFather) → lấy **TELEGRAM_TOKEN**.
- Thêm bot vào nhóm muốn nhận báo cáo → lấy **chat_id** của nhóm
  (gửi 1 tin trong nhóm rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates`).

### 4. GitHub — đẩy code + thêm Secrets
```bash
cd ~/projects/tuantu-index-bot
git init && git add . && git commit -m "init index bot"
gh repo create kelvinmarketer-alt/tuantu-index-bot --private --source=. --push
```
Vào **repo → Settings → Secrets and variables → Actions → New repository secret**, thêm:

| Secret | Giá trị |
|---|---|
| `SITE_URL` | `https://nongsantuantuhanoi.vn/` |
| `SITEMAP_URL` | `https://nongsantuantuhanoi.vn/sitemap.xml` |
| `SHEET_ID` | `1DMDPArnJKwz2qRHtDMDBG7kT0Clgm4UBq7PxaIvE6Tk` |
| `SHEET_TAB` | `Index Report` |
| `GCP_SA_JSON` | **dán nguyên nội dung file `sa.json`** |
| `TELEGRAM_TOKEN` | token bot |
| `TELEGRAM_CHAT_ID` | chat_id nhóm |
| `MAX_URLS` | `200` |

Xong! Bot tự chạy mỗi sáng. Muốn chạy thử ngay: **Actions → Index Check → Run workflow**.

---

## Chạy thử ở máy (tùy chọn)
```bash
cd ~/projects/tuantu-index-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # điền giá trị, GCP_SA_JSON dán JSON 1 dòng
export $(grep -v '^#' .env | xargs)
python index_bot.py
```

## Tinh chỉnh
- Đổi giờ chạy: sửa `cron` trong `.github/workflows/index-check.yml` (UTC).
- Tắt ép index, chỉ báo cáo: đặt secret `DO_SUBMIT_INDEXING=false`.
- Giới hạn số URL/lần: secret `MAX_URLS`.

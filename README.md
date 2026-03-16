# AccessForge

以 Django + PostgreSQL 建立的 Access 風格資料管理網站，支援自訂資料表、欄位定義、動態 CRUD 與 CSV 匯出，並可直接透過 Docker Compose 啟動。

## 快速開始

```bash
docker compose up -d --build
```

啟動後可使用：

- 網站首頁: http://localhost:8000/
- Django Admin: http://localhost:8000/admin/
- 預設管理員:
- 帳號: `admin`
- 密碼: `ChangeMe123!`

## 主要功能

- 建立資料表
- 定義欄位型別與順序
- 動態產生資料輸入表單
- 管理資料列的新增、修改、刪除
- 關鍵字搜尋
- 匯出 CSV
- Django Admin 後台

## 環境調整

請修改 [`.env`](/D:/Programs/AccessForge/.env) 中的帳號密碼與 Django 設定後再部署到正式環境。

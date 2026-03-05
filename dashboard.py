#!/usr/bin/env python3
"""
二手器材交易平台 - 本地管理儀表板
使用 Python 內建 http.server，不需要 Flask
啟動方式：python3 dashboard.py
打開瀏覽器：http://localhost:8888
"""

import http.server
import socketserver
import json
import sqlite3
import os
import sys
import urllib.parse
from pathlib import Path

# 支援命令列指定 port: python3 dashboard.py 9999
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "database" / "products.db"
MONITOR_DB_PATH = BASE_DIR / "database" / "monitor.db"
RULES_PATH = BASE_DIR / "config" / "group_rules.json"
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
IMAGES_DIR = BASE_DIR / "images" / "original"


def get_db(path=DB_PATH):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """自訂 HTTP handler，處理 API 和靜態檔案"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # API routes
        if path == "/":
            self.serve_html()
        elif path == "/api/products":
            self.api_products(query)
        elif path == "/api/groups":
            self.api_groups()
        elif path == "/api/post_log":
            self.api_post_log(query)
        elif path == "/api/stats":
            self.api_stats()
        elif path == "/api/monitor":
            self.api_monitor(query)
        elif path.startswith("/images/"):
            self.serve_image(path)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        data = json.loads(body) if body else {}

        if path == "/api/groups/toggle":
            self.api_toggle_group(data)
        elif path == "/api/products/update":
            self.api_update_product(data)
        elif path == "/api/products/sold":
            self.api_mark_sold(data)
        elif path == "/api/products/add":
            self.api_add_product(data)
        elif path == "/api/categories/add":
            self.api_add_category(data)
        else:
            self.send_error(404)

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def serve_image(self, path):
        # path like /images/original/xxx.jpg — 中文檔名需要 URL decode
        decoded_path = urllib.parse.unquote(path)
        file_path = BASE_DIR / decoded_path.lstrip("/")
        if file_path.exists():
            self.send_response(200)
            ext = file_path.suffix.lower()
            content_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
            self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, f"Image not found: {path}")

    def api_products(self, query):
        conn = get_db()
        cursor = conn.cursor()

        search = query.get("search", [""])[0]
        category = query.get("category", [""])[0]
        status_filter = query.get("status", [""])[0]
        sort_by = query.get("sort", ["id"])[0]
        page = int(query.get("page", ["1"])[0])
        per_page = 20

        where_clauses = []
        params = []

        if search:
            where_clauses.append("(product_name LIKE ? OR description LIKE ? OR image_path LIKE ? OR category LIKE ?)")
            params.extend([f"%{search}%"] * 4)
        if category:
            where_clauses.append("category = ?")
            params.append(category)
        if status_filter:
            where_clauses.append("status = ?")
            params.append(status_filter)

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Count
        cursor.execute(f"SELECT COUNT(*) FROM products{where_sql}", params)
        total = cursor.fetchone()[0]

        # Sort
        order_map = {
            "id": "id ASC",
            "name": "image_path ASC",
            "posted": "posted_count DESC",
            "recent": "last_posted DESC NULLS LAST",
        }
        order = order_map.get(sort_by, "id ASC")

        offset = (page - 1) * per_page
        cursor.execute(
            f"SELECT * FROM products{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        )
        products = [dict(row) for row in cursor.fetchall()]

        # Extract display name from image path
        for p in products:
            img = os.path.basename(p.get("image_path", ""))
            # Remove extension and numbers to get product type
            name = img.rsplit(".", 1)[0] if "." in img else img
            name = name.split("_")[0] if "_" in name else name
            p["display_name"] = name
            # URL encode 中文檔名
            raw_path = p.get("image_path", "")
            if raw_path:
                parts = raw_path.split("/")
                encoded_parts = [urllib.parse.quote(part) for part in parts]
                p["image_url"] = "/" + "/".join(encoded_parts)
            else:
                p["image_url"] = ""

        # Get unique categories from category column
        cursor.execute("SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")
        categories = [row[0] for row in cursor.fetchall()]

        conn.close()
        self.send_json({
            "products": products,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
            "categories": categories,
        })

    def api_groups(self):
        try:
            rules = load_json(RULES_PATH)
            groups = []
            for gid, info in rules.items():
                info["group_id"] = gid
                info["url"] = f"https://www.facebook.com/groups/{gid}"
                groups.append(info)
            # Sort: enabled first, then by category
            groups.sort(key=lambda g: (not g.get("enabled", True), g.get("category", ""), g.get("name", "")))
            self.send_json({"groups": groups, "total": len(groups)})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_toggle_group(self, data):
        group_id = data.get("group_id")
        enabled = data.get("enabled")
        if not group_id:
            self.send_json({"error": "missing group_id"}, 400)
            return
        try:
            rules = load_json(RULES_PATH)
            if group_id in rules:
                rules[group_id]["enabled"] = bool(enabled)
                save_json(RULES_PATH, rules)
                self.send_json({"ok": True, "group_id": group_id, "enabled": bool(enabled)})
            else:
                self.send_json({"error": "group not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_update_product(self, data):
        pid = data.get("id")
        if not pid:
            self.send_json({"error": "missing id"}, 400)
            return
        try:
            conn = get_db()
            cursor = conn.cursor()
            updates = []
            params = []
            for field in ["product_name", "description", "category", "price", "status"]:
                if field in data:
                    updates.append(f"{field} = ?")
                    params.append(data[field])
            if updates:
                params.append(pid)
                cursor.execute(f"UPDATE products SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()
            conn.close()
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_mark_sold(self, data):
        """標示商品為已出售 / 恢復為販售中"""
        pid = data.get("id")
        sold = data.get("sold", True)
        if not pid:
            self.send_json({"error": "missing id"}, 400)
            return
        try:
            conn = get_db()
            cursor = conn.cursor()
            if sold:
                from datetime import datetime
                cursor.execute(
                    "UPDATE products SET status = 'sold', sold_at = ? WHERE id = ?",
                    [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid]
                )
            else:
                cursor.execute(
                    "UPDATE products SET status = 'available', sold_at = NULL WHERE id = ?",
                    [pid]
                )
            conn.commit()
            conn.close()
            self.send_json({"ok": True, "id": pid, "status": "sold" if sold else "available"})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_add_product(self, data):
        """新增商品"""
        name = data.get("product_name", "")
        desc = data.get("description", "")
        category = data.get("category", "")
        price = data.get("price", 0)
        image_path = data.get("image_path", "")
        if not name and not category:
            self.send_json({"error": "請填寫商品名稱或類別"}, 400)
            return
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO products (product_name, description, category, price, image_path, status, posted_count)
                VALUES (?, ?, ?, ?, ?, 'available', 0)
            """, [name or category, desc, category, price, image_path])
            conn.commit()
            new_id = cursor.lastrowid
            conn.close()
            self.send_json({"ok": True, "id": new_id})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_add_category(self, data):
        """取得所有類別 / 新增類別 (透過新增一筆空商品)"""
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")
            categories = [row[0] for row in cursor.fetchall()]
            conn.close()
            self.send_json({"categories": categories})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_post_log(self, query):
        conn = get_db()
        cursor = conn.cursor()
        limit = int(query.get("limit", ["50"])[0])
        cursor.execute("""
            SELECT pl.*, p.image_path, p.product_name
            FROM post_log pl
            LEFT JOIN products p ON pl.product_id = p.id
            ORDER BY pl.posted_at DESC
            LIMIT ?
        """, [limit])
        logs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        self.send_json({"logs": logs, "total": len(logs)})

    def api_stats(self):
        conn = get_db()
        cursor = conn.cursor()

        # Products stats
        cursor.execute("SELECT COUNT(*) FROM products")
        total_products = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE posted_count > 0")
        posted_products = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(posted_count) FROM products")
        total_posts = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM products WHERE status = 'sold'")
        sold_products = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE status = 'available'")
        available_products = cursor.fetchone()[0]

        # Post log stats
        cursor.execute("SELECT COUNT(*) FROM post_log")
        total_log = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM post_log WHERE status = 'success'")
        success_log = cursor.fetchone()[0]

        # Groups
        try:
            rules = load_json(RULES_PATH)
            total_groups = len(rules)
            enabled_groups = sum(1 for g in rules.values() if g.get("enabled", True))
            categories = {}
            for g in rules.values():
                cat = g.get("category", "未分類")
                categories[cat] = categories.get(cat, 0) + 1
        except:
            total_groups = enabled_groups = 0
            categories = {}

        # Product type distribution by category
        cursor.execute("""
            SELECT category, COUNT(*) as cnt
            FROM products WHERE category != '' GROUP BY category ORDER BY cnt DESC
        """)
        product_dist = [{"name": row[0], "count": row[1]} for row in cursor.fetchall()]

        conn.close()
        self.send_json({
            "total_products": total_products,
            "posted_products": posted_products,
            "unposted_products": total_products - posted_products,
            "sold_products": sold_products,
            "available_products": available_products,
            "total_posts": total_posts,
            "total_log": total_log,
            "success_log": success_log,
            "total_groups": total_groups,
            "enabled_groups": enabled_groups,
            "group_categories": categories,
            "product_distribution": product_dist,
        })

    def api_monitor(self, query):
        if not MONITOR_DB_PATH.exists():
            self.send_json({"posts": [], "total": 0, "total_pages": 0, "page": 1, "message": "監控資料庫尚未建立，請先執行 monitor_groups.py"})
            return
        try:
            conn = get_db(MONITOR_DB_PATH)
            cursor = conn.cursor()
            search = query.get("search", [""])[0]
            min_price = query.get("min_price", [""])[0]
            max_price = query.get("max_price", [""])[0]
            group_cat = query.get("group_category", [""])[0]
            page = int(query.get("page", ["1"])[0])
            per_page = 20

            where = []
            params = []
            if search:
                where.append("(post_text LIKE ? OR product_name LIKE ? OR group_name LIKE ? OR author_name LIKE ?)")
                params.extend([f"%{search}%"] * 4)
            if min_price:
                where.append("price_numeric >= ?")
                params.append(float(min_price))
            if max_price:
                where.append("price_numeric <= ? AND price_numeric > 0")
                params.append(float(max_price))
            if group_cat:
                where.append("group_category = ?")
                params.append(group_cat)

            where_sql = " WHERE " + " AND ".join(where) if where else ""

            # Count
            cursor.execute(f"SELECT COUNT(*) FROM scraped_posts{where_sql}", params)
            total = cursor.fetchone()[0]

            # Paginated results
            offset = (page - 1) * per_page
            cursor.execute(
                f"SELECT * FROM scraped_posts{where_sql} ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            )
            posts = [dict(row) for row in cursor.fetchall()]

            # Clean up post_text for display
            for p in posts:
                text = p.get("post_text", "") or ""
                # Remove excessive whitespace and social media noise
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                clean_lines = []
                skip_patterns = ["讚", "回覆", "分享", "週", "天前", "小時", "留言", "則留言",
                                 "最相關", "所有留言", "查看更多", "顯示更多"]
                for line in lines:
                    # Skip short social media noise
                    if len(line) <= 5 and any(w in line for w in skip_patterns):
                        continue
                    # Skip lines that are just numbers + time units
                    if len(line) <= 6 and any(line.endswith(w) for w in ["週", "天", "小時", "分鐘"]):
                        continue
                    clean_lines.append(line)

                # If author_name is NULL, first line might be author
                if not p.get("author_name") and clean_lines:
                    if len(clean_lines[0]) <= 10 and len(clean_lines) > 1:
                        p["author_name"] = clean_lines[0]
                        clean_lines = clean_lines[1:]

                p["post_text_clean"] = "\n".join(clean_lines[:15])  # Max 15 lines
                p["post_text_short"] = clean_lines[0][:60] if clean_lines else "(無內容)"
                # Ensure price_numeric is a number for JS
                p["price_numeric"] = p.get("price_numeric") or 0

            conn.close()
            self.send_json({
                "posts": posts,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page,
            })
        except Exception as e:
            self.send_json({"error": str(e), "posts": [], "total": 0, "total_pages": 0, "page": 1})

    def serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = self.get_dashboard_html()
        self.wfile.write(html.encode("utf-8"))

    def get_dashboard_html(self):
        return '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>二手器材交易平台 - 管理儀表板</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
  --text: #f1f5f9; --text2: #94a3b8; --accent: #3b82f6;
  --accent2: #60a5fa; --green: #22c55e; --red: #ef4444;
  --orange: #f59e0b; --border: #475569;
}
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }
a { color: var(--accent2); text-decoration: none; }

/* Layout */
.sidebar {
  position: fixed; top: 0; left: 0; width: 220px; height: 100vh;
  background: var(--surface); border-right: 1px solid var(--border);
  padding: 20px 0; z-index: 100;
}
.sidebar h1 { font-size: 16px; padding: 0 20px 20px; color: var(--accent2); border-bottom: 1px solid var(--border); }
.sidebar h1 span { display: block; font-size: 11px; color: var(--text2); margin-top: 4px; font-weight: normal; }
.nav-item {
  display: block; padding: 12px 20px; color: var(--text2); cursor: pointer;
  border-left: 3px solid transparent; transition: all .2s;
}
.nav-item:hover { background: var(--surface2); color: var(--text); }
.nav-item.active { color: var(--accent2); border-left-color: var(--accent); background: rgba(59,130,246,.1); }
.nav-item .badge {
  float: right; background: var(--accent); color: #fff;
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
}
.main { margin-left: 220px; padding: 24px; min-height: 100vh; }
.page { display: none; }
.page.active { display: block; }

/* Stats Cards */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.stat-card {
  background: var(--surface); border-radius: 12px; padding: 20px;
  border: 1px solid var(--border);
}
.stat-card .label { font-size: 13px; color: var(--text2); margin-bottom: 8px; }
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .sub { font-size: 12px; color: var(--text2); margin-top: 4px; }
.stat-card.green .value { color: var(--green); }
.stat-card.blue .value { color: var(--accent2); }
.stat-card.orange .value { color: var(--orange); }

/* Toolbar */
.toolbar {
  display: flex; gap: 12px; margin-bottom: 20px; align-items: center; flex-wrap: wrap;
}
.toolbar input, .toolbar select {
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 8px 14px; border-radius: 8px; font-size: 14px;
}
.toolbar input { min-width: 250px; }
.toolbar select { min-width: 120px; }

/* Products Grid */
.products-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
.product-card {
  background: var(--surface); border-radius: 12px; overflow: hidden;
  border: 1px solid var(--border); transition: transform .2s, box-shadow .2s;
}
.product-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.3); }
.product-card img {
  width: 100%; height: 180px; object-fit: cover; background: var(--surface2);
}
.product-card .info { padding: 12px; }
.product-card .name { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
.product-card .desc { font-size: 12px; color: var(--text2); margin-bottom: 8px; }
.product-card .meta { display: flex; justify-content: space-between; font-size: 12px; align-items: center; }
.product-card.sold { opacity: .6; }
.product-card.sold img { filter: grayscale(.7); }
.product-card .sold-badge {
  position: absolute; top: 12px; right: 12px;
  background: var(--red); color: #fff; padding: 4px 12px;
  border-radius: 6px; font-size: 12px; font-weight: 700;
  z-index: 1;
}
.product-card .actions {
  display: flex; gap: 6px; margin-top: 8px;
}
.product-card .actions button {
  flex: 1; padding: 6px; border: 1px solid var(--border);
  background: var(--surface2); color: var(--text2);
  border-radius: 6px; font-size: 11px; cursor: pointer;
  font-family: inherit; transition: all .2s;
}
.product-card .actions button:hover { background: var(--accent); color: #fff; border-color: var(--accent); }
.product-card .actions button.sold-btn:hover { background: var(--red); border-color: var(--red); }
.product-card .actions button.restore-btn:hover { background: var(--green); border-color: var(--green); }

/* Add product modal */
.modal-bg {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,.6); z-index: 200; align-items: center; justify-content: center;
}
.modal-bg.show { display: flex; }
.modal-box {
  background: var(--surface); border-radius: 16px; padding: 32px;
  width: 90%; max-width: 520px; border: 1px solid var(--border);
}
.modal-box h3 { margin-bottom: 20px; font-size: 18px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 6px; }
.form-group input, .form-group select, .form-group textarea {
  width: 100%; padding: 10px 14px; background: var(--bg);
  border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; font-size: 14px; font-family: inherit;
}
.form-group textarea { resize: vertical; min-height: 80px; }
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
  outline: none; border-color: var(--accent);
}
.modal-actions { display: flex; gap: 12px; justify-content: flex-end; margin-top: 24px; }
.modal-actions button {
  padding: 10px 24px; border: none; border-radius: 8px;
  font-size: 14px; cursor: pointer; font-family: inherit;
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: #2563eb; }
.btn-cancel { background: var(--surface2); color: var(--text2); }
.btn-cancel:hover { background: var(--border); }
.tag {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 600;
}
.tag-green { background: rgba(34,197,94,.15); color: var(--green); }
.tag-orange { background: rgba(245,158,11,.15); color: var(--orange); }
.tag-red { background: rgba(239,68,68,.15); color: var(--red); }
.tag-blue { background: rgba(59,130,246,.15); color: var(--accent2); }

/* Groups Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; }
th { background: var(--surface2); text-align: left; padding: 12px 16px; font-size: 13px; color: var(--text2); white-space: nowrap; }
td { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 14px; }
tr:hover td { background: rgba(59,130,246,.05); }

/* Toggle Switch */
.toggle { position: relative; width: 44px; height: 24px; cursor: pointer; }
.toggle input { display: none; }
.toggle .slider {
  position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  background: var(--surface2); border-radius: 12px; transition: .3s;
}
.toggle .slider:before {
  content: ""; position: absolute; height: 18px; width: 18px;
  left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: .3s;
}
.toggle input:checked + .slider { background: var(--green); }
.toggle input:checked + .slider:before { transform: translateX(20px); }

/* Post Log */
.log-item {
  display: flex; gap: 16px; align-items: center;
  padding: 14px 16px; background: var(--surface); border-radius: 10px;
  margin-bottom: 8px; border: 1px solid var(--border);
}
.log-item img { width: 50px; height: 50px; border-radius: 8px; object-fit: cover; }
.log-item .details { flex: 1; }
.log-item .time { font-size: 12px; color: var(--text2); white-space: nowrap; }

/* Pagination */
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 24px; }
.pagination button {
  padding: 8px 16px; background: var(--surface); border: 1px solid var(--border);
  color: var(--text); border-radius: 8px; cursor: pointer; transition: .2s;
}
.pagination button:hover { background: var(--surface2); }
.pagination button.active { background: var(--accent); border-color: var(--accent); }
.pagination button:disabled { opacity: .4; cursor: not-allowed; }

/* Category bar chart */
.bar-chart { margin-top: 16px; }
.bar-row { display: flex; align-items: center; margin-bottom: 8px; }
.bar-label { width: 100px; font-size: 13px; color: var(--text2); text-align: right; padding-right: 12px; }
.bar-fill { height: 24px; border-radius: 4px; background: var(--accent); transition: width .5s; min-width: 2px; }
.bar-value { margin-left: 8px; font-size: 13px; color: var(--text2); }

/* Monitor - Card List Style */
.monitor-list { display: flex; flex-direction: column; gap: 12px; }
.monitor-item {
  background: var(--surface); border-radius: 12px; padding: 18px 20px;
  border: 1px solid var(--border); transition: box-shadow .2s;
}
.monitor-item:hover { box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.monitor-item-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px; gap: 12px; flex-wrap: wrap;
}
.monitor-item-no {
  background: var(--accent); color: #fff; font-size: 11px; font-weight: 700;
  width: 28px; height: 28px; border-radius: 50%; display: flex;
  align-items: center; justify-content: center; flex-shrink: 0;
}
.monitor-item-meta {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
}
.monitor-item-meta .group-tag {
  display: inline-block; padding: 3px 10px; border-radius: 20px;
  font-size: 11px; background: rgba(59,130,246,.1); color: var(--accent2); font-weight: 500;
}
.monitor-item-meta .author-tag {
  font-size: 12px; color: var(--text2);
}
.monitor-item-meta .time-tag {
  font-size: 11px; color: var(--text2); opacity: .7;
}
.monitor-item-body {
  font-size: 14px; color: var(--text); line-height: 1.7;
  max-height: 100px; overflow: hidden; position: relative;
  cursor: pointer; transition: max-height .3s; margin-bottom: 12px;
  white-space: pre-wrap; word-break: break-word;
}
.monitor-item-body.expanded { max-height: 800px; }
.monitor-item-body .fade {
  position: absolute; bottom: 0; left: 0; right: 0; height: 35px;
  background: linear-gradient(transparent, var(--surface));
  pointer-events: none;
}
.monitor-item-body.expanded .fade { display: none; }
.monitor-item-footer {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; padding-top: 10px;
  border-top: 1px solid var(--border);
}
.monitor-price {
  font-size: 18px; font-weight: 700; color: var(--green);
}
.monitor-price.no-price { color: var(--text2); font-size: 13px; font-weight: 400; }
.monitor-group-name {
  font-size: 12px; color: var(--text2); max-width: 250px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.monitor-link-btn {
  display: inline-block; padding: 6px 16px; border-radius: 6px;
  background: var(--surface2); color: var(--accent2); font-size: 12px;
  text-decoration: none; transition: all .2s; white-space: nowrap;
}
.monitor-link-btn:hover { background: var(--accent); color: #fff; }
.monitor-stats {
  display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap;
}
.monitor-stat {
  background: var(--surface); padding: 14px 20px; border-radius: 10px;
  border: 1px solid var(--border); min-width: 130px;
}
.monitor-stat .label { font-size: 11px; color: var(--text2); margin-bottom: 4px; }
.monitor-stat .val { font-size: 22px; font-weight: 700; }

/* Empty state */
.empty { text-align: center; padding: 60px 20px; color: var(--text2); }
.empty .icon { font-size: 48px; margin-bottom: 16px; }

@media (max-width: 768px) {
  .sidebar { width: 60px; }
  .sidebar h1, .nav-item span, .nav-item .badge { display: none; }
  .main { margin-left: 60px; }
  .products-grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
}
</style>
</head>
<body>

<div class="sidebar">
  <h1>二手器材平台<span>管理儀表板 v1.0</span></h1>
  <div class="nav-item active" onclick="showPage('dashboard')">
    <span>📊 總覽</span>
  </div>
  <div class="nav-item" onclick="showPage('products')">
    <span>📦 商品庫存</span>
    <span class="badge" id="badge-products">-</span>
  </div>
  <div class="nav-item" onclick="showPage('groups')">
    <span>👥 社團管理</span>
    <span class="badge" id="badge-groups">-</span>
  </div>
  <div class="nav-item" onclick="showPage('postlog')">
    <span>📝 發文紀錄</span>
  </div>
  <div class="nav-item" onclick="showPage('monitor')">
    <span>🔍 競品監控</span>
  </div>
</div>

<div class="main">
  <!-- Dashboard -->
  <div id="page-dashboard" class="page active">
    <h2 style="margin-bottom:20px;">📊 系統總覽</h2>
    <div class="stats-grid" id="stats-grid"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">
      <div style="background:var(--surface);border-radius:12px;padding:20px;border:1px solid var(--border);">
        <h3 style="margin-bottom:16px;font-size:15px;color:var(--text2);">商品類別分佈</h3>
        <div id="product-chart" class="bar-chart"></div>
      </div>
      <div style="background:var(--surface);border-radius:12px;padding:20px;border:1px solid var(--border);">
        <h3 style="margin-bottom:16px;font-size:15px;color:var(--text2);">社團類別分佈</h3>
        <div id="group-chart" class="bar-chart"></div>
      </div>
    </div>
  </div>

  <!-- Products -->
  <div id="page-products" class="page">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h2>📦 商品庫存</h2>
      <button onclick="showAddProduct()" style="padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-family:inherit;">＋ 新增商品</button>
    </div>
    <div class="toolbar">
      <input type="text" id="product-search" placeholder="搜尋商品名稱..." oninput="debounceProducts()">
      <select id="product-category" onchange="loadProducts()">
        <option value="">全部類別</option>
      </select>
      <select id="product-status" onchange="loadProducts()">
        <option value="">全部狀態</option>
        <option value="available">販售中</option>
        <option value="sold">已出售</option>
      </select>
      <select id="product-sort" onchange="loadProducts()">
        <option value="id">預設排序</option>
        <option value="name">按名稱</option>
        <option value="posted">最多發文</option>
        <option value="recent">最近發文</option>
      </select>
    </div>
    <div id="products-container" class="products-grid"></div>
    <div id="products-pagination" class="pagination"></div>
  </div>

  <!-- Groups -->
  <div id="page-groups" class="page">
    <h2 style="margin-bottom:20px;">👥 社團管理</h2>
    <div class="toolbar">
      <input type="text" id="group-search" placeholder="搜尋社團名稱..." oninput="filterGroups()">
      <select id="group-category-filter" onchange="filterGroups()">
        <option value="">全部類別</option>
      </select>
    </div>
    <div class="table-wrap">
      <table id="groups-table">
        <thead>
          <tr>
            <th>啟用</th>
            <th>社團名稱</th>
            <th>成員數</th>
            <th>類別</th>
            <th>發文限制</th>
            <th>注意事項</th>
          </tr>
        </thead>
        <tbody id="groups-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Post Log -->
  <div id="page-postlog" class="page">
    <h2 style="margin-bottom:20px;">📝 發文紀錄</h2>
    <div id="postlog-container"></div>
  </div>

  <!-- Monitor -->
  <div id="page-monitor" class="page">
    <h2 style="margin-bottom:20px;">🔍 競品監控</h2>
    <div id="monitor-stats-bar" class="monitor-stats"></div>
    <div class="toolbar">
      <input type="text" id="monitor-search" placeholder="搜尋貼文內容、產品、社團..." oninput="debounceMonitor()" style="min-width:300px;">
      <input type="number" id="monitor-min" placeholder="最低價" style="width:100px;">
      <input type="number" id="monitor-max" placeholder="最高價" style="width:100px;">
      <select id="monitor-cat" onchange="loadMonitor()">
        <option value="">全部類別</option>
        <option value="餐飲器材">餐飲器材</option>
        <option value="二手家具">二手家具</option>
        <option value="二手綜合">二手綜合</option>
      </select>
      <button onclick="loadMonitor()" style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer;">搜尋</button>
    </div>
    <div class="monitor-list" id="monitor-list"></div>
    <div id="monitor-pagination" class="pagination"></div>
  </div>
</div>

<!-- Add Product Modal -->
<div class="modal-bg" id="addProductModal">
  <div class="modal-box">
    <h3>＋ 新增商品</h3>
    <div class="form-group">
      <label>商品類別</label>
      <div style="display:flex;gap:8px;">
        <select id="add-category" style="flex:1;" onchange="toggleNewCategory()">
          <option value="">選擇類別...</option>
        </select>
        <button onclick="toggleNewCategory(true)" style="padding:8px 14px;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:8px;cursor:pointer;font-size:13px;white-space:nowrap;">＋ 新類別</button>
      </div>
      <input type="text" id="add-new-category" placeholder="輸入新類別名稱..." style="display:none;margin-top:8px;">
    </div>
    <div class="form-group">
      <label>商品名稱</label>
      <input type="text" id="add-name" placeholder="例如：三門冷藏冰箱">
    </div>
    <div class="form-group">
      <label>商品描述</label>
      <textarea id="add-desc" placeholder="品牌、規格、尺寸、新舊程度等..."></textarea>
    </div>
    <div class="form-group">
      <label>售價 (NT$)</label>
      <input type="number" id="add-price" placeholder="0 表示議價">
    </div>
    <div class="form-group">
      <label>圖片路徑（可稍後補）</label>
      <input type="text" id="add-image" placeholder="images/original/xxx.jpg">
    </div>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAddProduct()">取消</button>
      <button class="btn-primary" onclick="submitAddProduct()">新增商品</button>
    </div>
  </div>
</div>

<!-- Edit Product Modal -->
<div class="modal-bg" id="editProductModal">
  <div class="modal-box">
    <h3>編輯商品</h3>
    <input type="hidden" id="edit-id">
    <div class="form-group">
      <label>商品類別</label>
      <select id="edit-category"></select>
    </div>
    <div class="form-group">
      <label>商品名稱</label>
      <input type="text" id="edit-name">
    </div>
    <div class="form-group">
      <label>商品描述</label>
      <textarea id="edit-desc"></textarea>
    </div>
    <div class="form-group">
      <label>售價 (NT$)</label>
      <input type="number" id="edit-price">
    </div>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeEditProduct()">取消</button>
      <button class="btn-primary" onclick="submitEditProduct()">儲存</button>
    </div>
  </div>
</div>

<script>
// State
let allGroups = [];
let currentProductPage = 1;
let debounceTimer = null;

// Navigation
function showPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelectorAll('.nav-item')[
    ['dashboard','products','groups','postlog','monitor'].indexOf(page)
  ].classList.add('active');

  if (page === 'products') loadProducts();
  if (page === 'groups') loadGroups();
  if (page === 'postlog') loadPostLog();
  if (page === 'monitor') loadMonitor();
}

// Dashboard
async function loadStats() {
  const res = await fetch('/api/stats');
  const d = await res.json();

  document.getElementById('badge-products').textContent = d.total_products;
  document.getElementById('badge-groups').textContent = d.total_groups;

  document.getElementById('stats-grid').innerHTML = `
    <div class="stat-card blue">
      <div class="label">商品總數</div>
      <div class="value">${d.total_products}</div>
      <div class="sub">販售中 ${d.available_products} / 已出售 ${d.sold_products}</div>
    </div>
    <div class="stat-card green">
      <div class="label">啟用社團</div>
      <div class="value">${d.enabled_groups}</div>
      <div class="sub">總計 ${d.total_groups} 個社團</div>
    </div>
    <div class="stat-card orange">
      <div class="label">累計發文</div>
      <div class="value">${d.total_posts}</div>
      <div class="sub">成功 ${d.success_log} / 總共 ${d.total_log} 筆</div>
    </div>
    <div class="stat-card" style="${d.sold_products > 0 ? 'border-color:var(--green);' : ''}">
      <div class="label">已出售商品</div>
      <div class="value" style="color:var(--green);">${d.sold_products}</div>
      <div class="sub">出售率 ${d.total_products > 0 ? Math.round(d.sold_products / d.total_products * 100) : 0}%</div>
    </div>
  `;

  // Product chart
  const maxP = Math.max(...d.product_distribution.map(x => x.count), 1);
  document.getElementById('product-chart').innerHTML = d.product_distribution.map(x => `
    <div class="bar-row">
      <div class="bar-label">${x.name}</div>
      <div class="bar-fill" style="width:${x.count/maxP*100}%"></div>
      <div class="bar-value">${x.count}</div>
    </div>
  `).join('');

  // Group chart
  const cats = Object.entries(d.group_categories);
  const maxG = Math.max(...cats.map(([,v]) => v), 1);
  const colors = ['#3b82f6','#22c55e','#f59e0b','#ef4444','#a855f7'];
  document.getElementById('group-chart').innerHTML = cats.map(([k,v], i) => `
    <div class="bar-row">
      <div class="bar-label">${k}</div>
      <div class="bar-fill" style="width:${v/maxG*100}%;background:${colors[i%colors.length]}"></div>
      <div class="bar-value">${v}</div>
    </div>
  `).join('');
}

// Products
function debounceProducts() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { currentProductPage = 1; loadProducts(); }, 300);
}

async function loadProducts(page) {
  if (page) currentProductPage = page;
  const search = document.getElementById('product-search').value;
  const cat = document.getElementById('product-category').value;
  const status = document.getElementById('product-status').value;
  const sort = document.getElementById('product-sort').value;
  const params = new URLSearchParams({ search, category: cat, status, sort, page: currentProductPage });
  const res = await fetch('/api/products?' + params);
  const d = await res.json();

  // Populate category filter
  const sel = document.getElementById('product-category');
  if (sel.options.length <= 1 && d.categories) {
    d.categories.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });
  }

  const container = document.getElementById('products-container');
  if (d.products.length === 0) {
    container.innerHTML = '<div class="empty"><div class="icon">📦</div><p>沒有找到商品</p></div>';
  } else {
    container.innerHTML = d.products.map(p => `
      <div class="product-card ${p.status === 'sold' ? 'sold' : ''}" style="position:relative;">
        ${p.status === 'sold' ? '<div class="sold-badge">已出售</div>' : ''}
        <img src="${p.image_url}" alt="${p.display_name}" loading="lazy"
             onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 200 200%22><rect fill=%22%23334155%22 width=%22200%22 height=%22200%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22%2394a3b8%22 text-anchor=%22middle%22 dy=%22.3em%22 font-size=%2220%22>No Image</text></svg>'">
        <div class="info">
          <div class="name">${p.display_name}</div>
          <div class="desc">${p.category || p.product_name} #${p.id} ${p.price > 0 ? '· NT$' + p.price.toLocaleString() : ''}</div>
          <div class="meta">
            <span class="tag ${p.posted_count > 0 ? 'tag-green' : 'tag-orange'}">
              ${p.posted_count > 0 ? '已發 ' + p.posted_count + ' 次' : '未發文'}
            </span>
            <span class="tag ${p.status === 'sold' ? 'tag-red' : 'tag-blue'}">
              ${p.status === 'sold' ? '已出售' : '販售中'}
            </span>
          </div>
          <div class="actions">
            <button onclick="editProduct(${p.id}, '${(p.product_name||'').replace(/'/g,"\\'")}', '${(p.description||'').replace(/'/g,"\\'")}', '${p.category||''}', ${p.price||0})">編輯</button>
            ${p.status === 'sold'
              ? `<button class="restore-btn" onclick="markSold(${p.id}, false)">恢復販售</button>`
              : `<button class="sold-btn" onclick="markSold(${p.id}, true)">標示已售</button>`
            }
          </div>
        </div>
      </div>
    `).join('');
  }

  // Pagination
  const pag = document.getElementById('products-pagination');
  if (d.total_pages > 1) {
    let html = `<button ${d.page <= 1 ? 'disabled' : ''} onclick="loadProducts(${d.page-1})">上一頁</button>`;
    for (let i = 1; i <= d.total_pages; i++) {
      html += `<button class="${i===d.page?'active':''}" onclick="loadProducts(${i})">${i}</button>`;
    }
    html += `<button ${d.page >= d.total_pages ? 'disabled' : ''} onclick="loadProducts(${d.page+1})">下一頁</button>`;
    pag.innerHTML = html;
  } else {
    pag.innerHTML = '';
  }
}

// Groups
async function loadGroups() {
  const res = await fetch('/api/groups');
  const d = await res.json();
  allGroups = d.groups;

  // Populate category filter
  const cats = [...new Set(allGroups.map(g => g.category))];
  const sel = document.getElementById('group-category-filter');
  if (sel.options.length <= 1) {
    cats.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });
  }
  renderGroups(allGroups);
}

function filterGroups() {
  const search = document.getElementById('group-search').value.toLowerCase();
  const cat = document.getElementById('group-category-filter').value;
  const filtered = allGroups.filter(g => {
    if (search && !g.name.toLowerCase().includes(search)) return false;
    if (cat && g.category !== cat) return false;
    return true;
  });
  renderGroups(filtered);
}

function renderGroups(groups) {
  document.getElementById('groups-tbody').innerHTML = groups.map(g => `
    <tr>
      <td>
        <label class="toggle">
          <input type="checkbox" ${g.enabled ? 'checked' : ''} onchange="toggleGroup('${g.group_id}', this.checked)">
          <span class="slider"></span>
        </label>
      </td>
      <td>
        <a href="${g.url}" target="_blank" style="font-weight:600;">${g.name}</a>
        <div style="font-size:11px;color:var(--text2);margin-top:2px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          ${g.rules || ''}
        </div>
      </td>
      <td>${g.members || '-'}</td>
      <td><span class="tag tag-blue">${g.category}</span></td>
      <td style="font-size:12px;color:var(--orange);">${g.frequency_limit || '-'}</td>
      <td style="font-size:12px;color:var(--red);max-width:200px;">${g.warning || '-'}</td>
    </tr>
  `).join('');
}

async function toggleGroup(gid, enabled) {
  await fetch('/api/groups/toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({group_id: gid, enabled})
  });
}

// Post Log
async function loadPostLog() {
  const res = await fetch('/api/post_log');
  const d = await res.json();
  const container = document.getElementById('postlog-container');
  if (d.logs.length === 0) {
    container.innerHTML = '<div class="empty"><div class="icon">📝</div><p>尚無發文紀錄</p><p style="margin-top:8px;font-size:13px;">執行 start_posting_now.py 開始自動發文</p></div>';
    return;
  }
  container.innerHTML = d.logs.map(l => `
    <div class="log-item">
      ${l.image_path ? `<img src="/${l.image_path}" onerror="this.style.display='none'">` : ''}
      <div class="details">
        <div style="font-weight:600;">${l.group_name || l.group_url || '-'}</div>
        <div style="font-size:12px;color:var(--text2);margin-top:2px;">${(l.caption_used || '').substring(0, 100)}...</div>
      </div>
      <span class="tag ${l.status === 'success' ? 'tag-green' : 'tag-red'}">${l.status}</span>
      <div class="time">${l.posted_at || '-'}</div>
    </div>
  `).join('');
}

// Monitor
let monitorPage = 1;

function debounceMonitor() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { monitorPage = 1; loadMonitor(); }, 300);
}

async function loadMonitor(page) {
  if (page) monitorPage = page;
  const search = document.getElementById('monitor-search').value;
  const min_price = document.getElementById('monitor-min').value;
  const max_price = document.getElementById('monitor-max').value;
  const group_cat = document.getElementById('monitor-cat').value;
  const params = new URLSearchParams({ page: monitorPage });
  if (search) params.set('search', search);
  if (min_price) params.set('min_price', min_price);
  if (max_price) params.set('max_price', max_price);
  if (group_cat) params.set('group_category', group_cat);

  const res = await fetch('/api/monitor?' + params);
  const d = await res.json();
  const listEl = document.getElementById('monitor-list');
  const statsBar = document.getElementById('monitor-stats-bar');

  if (d.message) {
    listEl.innerHTML = `<div style="text-align:center;padding:60px;color:var(--text2);">🔍 ${d.message}</div>`;
    statsBar.innerHTML = '';
    document.getElementById('monitor-pagination').innerHTML = '';
    return;
  }

  // Stats bar
  const withPrice = d.posts.filter(p => p.price_numeric > 0);
  const avgPrice = withPrice.length > 0 ? Math.round(withPrice.reduce((s,p) => s + p.price_numeric, 0) / withPrice.length) : 0;
  const minP = withPrice.length > 0 ? Math.min(...withPrice.map(p => p.price_numeric)) : 0;
  const maxP = withPrice.length > 0 ? Math.max(...withPrice.map(p => p.price_numeric)) : 0;
  statsBar.innerHTML = `
    <div class="monitor-stat"><div class="label">搜尋結果</div><div class="val" style="color:var(--accent2);">${d.total}</div></div>
    <div class="monitor-stat"><div class="label">有標價</div><div class="val" style="color:var(--green);">${withPrice.length}</div></div>
    ${avgPrice > 0 ? `<div class="monitor-stat"><div class="label">平均價格</div><div class="val" style="color:var(--orange);">$${avgPrice.toLocaleString()}</div></div>` : ''}
    ${minP > 0 ? `<div class="monitor-stat"><div class="label">最低 / 最高</div><div class="val" style="font-size:14px;">$${minP.toLocaleString()} ~ $${maxP.toLocaleString()}</div></div>` : ''}
  `;

  if (d.posts.length === 0) {
    listEl.innerHTML = `<div style="text-align:center;padding:60px;color:var(--text2);">沒有找到競品資料</div>`;
    document.getElementById('monitor-pagination').innerHTML = '';
    return;
  }

  const startNo = (d.page - 1) * (d.per_page || 20);
  listEl.innerHTML = d.posts.map((p, i) => {
    const content = (p.post_text_clean || p.post_text || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const priceDisplay = p.price_numeric > 0
      ? `<span class="monitor-price">NT$ ${p.price_numeric.toLocaleString()}</span>`
      : (p.price ? `<span class="monitor-price" style="font-size:14px;color:var(--orange);">${p.price}</span>` : '<span class="monitor-price no-price">未標價</span>');
    const timeStr = p.scraped_at ? new Date(p.scraped_at).toLocaleDateString('zh-TW') : '';

    return `<div class="monitor-item">
      <div class="monitor-item-header">
        <div style="display:flex;align-items:center;gap:10px;">
          <span class="monitor-item-no">${startNo + i + 1}</span>
          <div class="monitor-item-meta">
            <span class="group-tag">${p.group_category || '未分類'}</span>
            <span class="author-tag">👤 ${p.author_name || '未知'}</span>
            <span class="time-tag">${timeStr}</span>
          </div>
        </div>
        ${priceDisplay}
      </div>
      <div class="monitor-item-body" onclick="this.classList.toggle('expanded')">
        ${content}
        <div class="fade"></div>
      </div>
      <div class="monitor-item-footer">
        <span class="monitor-group-name">📍 ${p.group_name || ''}</span>
        ${p.post_url ? `<a href="${p.post_url}" target="_blank" class="monitor-link-btn">🔗 查看原文</a>` : ''}
      </div>
    </div>`;
  }).join('');

  // Pagination
  const pag = document.getElementById('monitor-pagination');
  if (d.total_pages > 1) {
    let html = `<button ${d.page <= 1 ? 'disabled' : ''} onclick="loadMonitor(${d.page-1})">上一頁</button>`;
    const start = Math.max(1, d.page - 3);
    const end = Math.min(d.total_pages, d.page + 3);
    for (let i = start; i <= end; i++) {
      html += `<button class="${i===d.page?'active':''}" onclick="loadMonitor(${i})">${i}</button>`;
    }
    html += `<button ${d.page >= d.total_pages ? 'disabled' : ''} onclick="loadMonitor(${d.page+1})">下一頁</button>`;
    html += `<span style="color:var(--text2);font-size:13px;padding:8px;">共 ${d.total} 筆 / ${d.total_pages} 頁</span>`;
    pag.innerHTML = html;
  } else {
    pag.innerHTML = d.total > 0 ? `<span style="color:var(--text2);font-size:13px;">共 ${d.total} 筆</span>` : '';
  }
}

// ===== MARK SOLD =====
async function markSold(id, sold) {
  const action = sold ? '確定要標示此商品為已出售？' : '確定要恢復此商品為販售中？';
  if (!confirm(action)) return;
  await fetch('/api/products/sold', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ id, sold })
  });
  loadProducts();
  loadStats();
}

// ===== ADD PRODUCT =====
let allCategories = [];

async function showAddProduct() {
  // Load categories
  const res = await fetch('/api/categories/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
  const d = await res.json();
  allCategories = d.categories || [];
  const sel = document.getElementById('add-category');
  sel.innerHTML = '<option value="">選擇類別...</option>' +
    allCategories.map(c => `<option value="${c}">${c}</option>`).join('');
  document.getElementById('add-new-category').style.display = 'none';
  document.getElementById('add-name').value = '';
  document.getElementById('add-desc').value = '';
  document.getElementById('add-price').value = '';
  document.getElementById('add-image').value = '';
  document.getElementById('addProductModal').classList.add('show');
}

function closeAddProduct() {
  document.getElementById('addProductModal').classList.remove('show');
}

function toggleNewCategory(show) {
  const input = document.getElementById('add-new-category');
  if (show) {
    input.style.display = 'block';
    input.focus();
    document.getElementById('add-category').value = '';
  } else {
    input.style.display = 'none';
  }
}

async function submitAddProduct() {
  let category = document.getElementById('add-category').value;
  const newCat = document.getElementById('add-new-category').value.trim();
  if (newCat) category = newCat;

  const data = {
    product_name: document.getElementById('add-name').value.trim() || category,
    description: document.getElementById('add-desc').value.trim(),
    category: category,
    price: parseInt(document.getElementById('add-price').value) || 0,
    image_path: document.getElementById('add-image').value.trim()
  };

  if (!data.product_name && !data.category) {
    alert('請至少填寫商品名稱或類別');
    return;
  }

  const res = await fetch('/api/products/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  const result = await res.json();
  if (result.ok) {
    closeAddProduct();
    loadProducts();
    loadStats();
    alert('商品新增成功！ID: ' + result.id);
  } else {
    alert('新增失敗: ' + (result.error || '未知錯誤'));
  }
}

// ===== EDIT PRODUCT =====
function editProduct(id, name, desc, category, price) {
  document.getElementById('edit-id').value = id;
  document.getElementById('edit-name').value = name;
  document.getElementById('edit-desc').value = desc;
  document.getElementById('edit-price').value = price;

  const sel = document.getElementById('edit-category');
  sel.innerHTML = allCategories.map(c =>
    `<option value="${c}" ${c === category ? 'selected' : ''}>${c}</option>`
  ).join('') + '<option value="">（無分類）</option>';

  document.getElementById('editProductModal').classList.add('show');
}

function closeEditProduct() {
  document.getElementById('editProductModal').classList.remove('show');
}

async function submitEditProduct() {
  const data = {
    id: parseInt(document.getElementById('edit-id').value),
    product_name: document.getElementById('edit-name').value.trim(),
    description: document.getElementById('edit-desc').value.trim(),
    category: document.getElementById('edit-category').value,
    price: parseInt(document.getElementById('edit-price').value) || 0
  };
  await fetch('/api/products/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  closeEditProduct();
  loadProducts();
}

// Close modals on ESC
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeAddProduct();
    closeEditProduct();
  }
});

// Init - also preload categories
async function init() {
  loadStats();
  try {
    const res = await fetch('/api/categories/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    const d = await res.json();
    allCategories = d.categories || [];
  } catch(e) {}
}
init();
</script>
</body>
</html>'''

    def log_message(self, format, *args):
        # Suppress access logs for cleaner output
        pass


def main():
    os.chdir(str(BASE_DIR))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), DashboardHandler) as httpd:
        print(f"=" * 50)
        print(f"  二手器材交易平台 - 管理儀表板")
        print(f"  http://localhost:{PORT}")
        print(f"=" * 50)
        print(f"  商品圖片: {IMAGES_DIR}")
        print(f"  資料庫:   {DB_PATH}")
        print(f"  社團規則: {RULES_PATH}")
        print(f"=" * 50)
        print(f"  按 Ctrl+C 停止伺服器")
        print()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n伺服器已停止")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""AI 每日摘要 — Web 服务器。

HTTP API(纯 stdlib http.server, 无第三方依赖):
  GET  /                 前端页面
  GET  /api/days         所有 day(倒序)
  GET  /api/days/{day}   某天的论文
  GET  /api/categories   类别聚合
  GET  /api/stats        全局统计
  POST /api/refresh      触发抓取(抓 arXiv 近 min_days 天, 每 day 覆盖更新)
  GET  /api/health       健康检查

数据层在 backend.py。同一 day 覆盖更新。
"""
from __future__ import annotations
import os, json, threading
import urllib.parse
import http.server, socketserver
from datetime import datetime, timezone

import backend as B

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
HOST = os.environ.get("AI_DAILY_HOST", "127.0.0.1")
PORT = int(os.environ.get("AI_DAILY_PORT", "8888"))
MIN_DAYS = int(os.environ.get("AI_DAILY_MIN_DAYS", "3"))
# 默认刷新模式: "backfill" 长窗口(确保无遗漏), "focus" 短窗口(每日聚焦)
BACKFILL_DEFAULT_MODE = os.environ.get("AI_DAILY_DEFAULT_MODE", "backfill")

# 抓取锁, 防止并发刷新
_refresh_lock = threading.Lock()
_last_refresh = {}


def _send_json(res, code, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    res.send_response(code)
    res.headers["Content-Type"] = "application/json; charset=utf-8"
    res.headers["Content-Length"] = str(len(data))
    res.end_headers()
    res.wfile.write(data)


def _send_file(res, code, path, ctype="text/html; charset=utf-8"):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        _send_json(res, 404, {"error": "not found"})
        return
    res.send_response(code)
    res.headers["Content-Type"] = ctype
    res.headers["Content-Length"] = str(len(data))
    res.end_headers()
    res.wfile.write(data)


class AppServer(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 降噪

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/" or path == "/index.html":
            _send_file(self, 200, os.path.join(WEB_DIR, "index.html"))
            return
        if path == "/static/css.css":
            _send_file(self, 200, os.path.join(WEB_DIR, "css.css"),
                       "text/css; charset=utf-8")
            return
        if path == "/static/app.js":
            _send_file(self, 200, os.path.join(WEB_DIR, "app.js"),
                       "application/javascript; charset=utf-8")
            return
        if path == "/api/days":
            _send_json(self, 200, {"days": B.list_days()})
            return
        if path == "/api/days/count":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            cat = q.get("cat", [""] )[0]
            _send_json(self, 200, {"counts": B.count_by_day_and_cat(B.list_days(), cat)})
            return
        if path == "/api/categories":
            _send_json(self, 200, {"categories": B.get_categories()})
            return
        if path.startswith("/api/days/"):
            day = path[len("/api/days/"):]
            rows = B.get_papers_for_day(day)
            _send_json(self, 200, {"day": day, "papers": rows, "count": len(rows)})
            return
        if path == "/api/stats":
            days = B.list_days()
            total = sum(B.count_for_day(d) for d in days)
            _send_json(self, 200, {
                "days": days,
                "total_papers": total,
                "arxiv_categories": list(B.ARXIV_CATEGORIES.keys()),
                "min_days": MIN_DAYS,
                "backfill_days": B.BACKFILL_DAYS,
                "default_mode": BACKFILL_DEFAULT_MODE,
                "last_refresh": _last_refresh,
            })
            return
        if path == "/api/health":
            _send_json(self, 200, {"ok": True})
            return
        _send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/refresh":
            with _refresh_lock:
                t0 = datetime.now(timezone.utc)
                try:
                    # 支持 query 参数 ?mode=focus|backfill
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    mode = q.get("mode", [BACKFILL_DEFAULT_MODE])[0]
                    by_day, counts = B.fetch_all(min_days=MIN_DAYS, mode=mode)
                    saved = {}
                    for d, papers in by_day.items():
                        saved[d] = B.save_day(d, papers)
                    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
                    global _last_refresh
                    _last_refresh = {
                        "ts": t0.strftime("%Y-%m-%d %H:%M:%S%z"),
                        "mode": mode,
                        "saved": saved,
                        "counts": counts,
                        "elapsed_s": round(elapsed, 1),
                    }
                    _send_json(self, 200, _last_refresh)
                except Exception as e:
                    _send_json(self, 500, {"error": str(e)})
        else:
            _send_json(self, 405, {"error": "method not allowed"})


def main():
    B.init_db()
    # 首次启动确保有数据
    if not B.list_days():
        by_day, _ = B.fetch_all(min_days=MIN_DAYS)
        for d, papers in by_day.items():
            B.save_day(d, papers)
            print(f"  初始化: 写入 {d} -> {len(papers)} 条")
    print(f"AI 每日摘要 Web 服务器 启动于 http://{HOST}:{PORT}")
    print(f"  数据: {B.DB_PATH}")
    print(f"  源:  {list(B.ARXIV_CATEGORIES)}")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), AppServer) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    main()

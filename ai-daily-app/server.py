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
import os, json, threading, time, argparse
import urllib.parse
import http.server, socketserver
import logging
from datetime import datetime, timezone

import backend as B

# ─── 结构化日志 ─────────────────────────────────────────
def _json_fmt():
    """JSON 行格式化(结构化): 每行一个可被机器解析的对象。"""
    class _JSON(logging.Formatter):
        def format(self, record):
            return json.dumps({
                "ts": record.created,
                "time": record.ctime,
                "level": record.levelname,
                "component": record.name,
                "message": record.getMessage(),
            }, ensure_ascii=False, default=str)
    return _JSON()


def _set_up_logging(level: str = "INFO", log_file: str = "") -> None:
    """配置根日志器 'ai-daily'。
    - 默认输出 console(固定开启);
    - 若给 log_file(启动参数 --log-file 或 环境变量 AI_DAILY_LOG_FILE),
      同时追加写入本地文件;
    - 级别由 AI_DAILY_LOG_LEVEL 控制(默认 INFO);
    - AI_DAILY_LOG_JSON=1 时用 JSON 行(纯结构化)输出。"""
    name = "ai-daily"
    lvl = getattr(logging, level.upper(), logging.INFO)
    lg = logging.getLogger(name)
    lg.setLevel(lvl)
    if lg.handlers:
        return
    use_json = os.environ.get("AI_DAILY_LOG_JSON", "").lower() in ("1", "true", "yes")
    if use_json:
        fmt = _json_fmt()
    else:
        # 纯文本结构化: 时间(ISO) 级别 [组件] 消息
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z")
    sh = logging.StreamHandler()  # console 始终开启
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    if log_file:
        try:
            lg_dir = os.path.dirname(os.path.abspath(log_file))
            os.makedirs(lg_dir, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            lg.addHandler(fh)
        except Exception as e:
            lg.warning(f"文件日志器初始化失败 {log_file}: {e}")

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
LOG = logging.getLogger("ai-daily")


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
    # 关闭 keep-alive: HTTP/1.0 每次响应后关连接,
    # 避免浏览器长连接卡在 handle_one_request 的 readline,
    # 使 Ctrl+C 可快速干净退出
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass  # 降噪

    def setup(self):
        super().setup()
        # 连接超时: readline 不再无限卡, 慢/残缺连接快速回收
        try:
            self.connection.settimeout(10)
        except Exception:
            pass

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                _send_file(self, 200, os.path.join(WEB_DIR, "index.html"))
                LOG.info("GET 页面 index.html")
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
                days = B.list_days()
                _send_json(self, 200, {"days": days})
                LOG.info("GET /api/days 返回 %s 天", len(days))
                return
            if path == "/api/days/count":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                cat = q.get("cat", [""] )[0]
                _send_json(self, 200, {"counts": B.count_by_day_and_cat(B.list_days(), cat)})
                LOG.info("GET /api/days/count cat=%s", cat)
                return
            if path == "/api/categories":
                _send_json(self, 200, {"categories": B.get_categories()})
                LOG.info("GET /api/categories 返回 %s 类", B.get_categories())
                return
            if path.startswith("/api/days/"):
                day = path[len("/api/days/"):]
                rows = B.get_papers_for_day(day)
                _send_json(self, 200, {"day": day, "papers": rows, "count": len(rows)})
                LOG.info("GET /api/days/%s 返回 %s 篇", day, len(rows))
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
                LOG.info("GET /api/stats total_papers=%s", total)
                return
            if path == "/api/health":
                _send_json(self, 200, {"ok": True})
                return
            _send_json(self, 404, {"error": "not found"})
            LOG.warning("GET %s 404", path)
        except Exception as e:
            LOG.exception("GET %s 异常: %s", path, e)
            _send_json(self, 500, {"error": str(e)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/refresh":
            with _refresh_lock:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                mode = q.get("mode", [BACKFILL_DEFAULT_MODE])[0]
                LOG.info("POST /api/refresh 开始 mode=%s", mode)
                t0 = time.time()
                try:
                    by_day, counts = B.fetch_all(min_days=MIN_DAYS, mode=mode)
                    saved = {}
                    for d, papers in by_day.items():
                        saved[d] = B.save_day(d, papers)
                    total = sum(len(v) for v in by_day.values())
                    elapsed = time.time() - t0
                    LOG.info("POST /api/refresh 完成 mode=%s 源=%s 合并=%s 保存=%s 耗时=%ss",
                             mode, len(counts), total, {d: saved[d] for d in sorted(saved)}, round(elapsed, 1))
                    global _last_refresh
                    _last_refresh = {
                        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z"),
                        "mode": mode,
                        "saved": saved,
                        "counts": counts,
                        "elapsed_s": round(elapsed, 1),
                    }
                    _send_json(self, 200, _last_refresh)
                except Exception as e:
                    LOG.exception("POST /api/refresh 异常 mode=%s: %s", mode, e)
                    _send_json(self, 500, {"error": str(e)})
        else:
            _send_json(self, 405, {"error": "method not allowed"})


def main():
    # ─── CLI 参数 ───
    # 默认输出 console; 可通过 --log-file 追加写入本地文件,
    # --log-level 控制级别, --log-json 输出纯 JSON 行(机器解析)。
    # 环境变量: AI_DAILY_LOG_LEVEL / AI_DAILY_LOG_FILE / AI_DAILY_LOG_JSON
    parser = argparse.ArgumentParser(description="AI 每日摘要 Web 服务器")
    parser.add_argument("--log-file", default="",
                        help="追加写入日志的本地文件路径(默认只输出 console)")
    parser.add_argument("--log-level", default=os.environ.get("AI_DAILY_LOG_LEVEL", "INFO"),
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="日志级别(默认 INFO, 可通过环境变量覆盖)")
    parser.add_argument("--log-json", action="store_true",
                        help="输出纯 JSON 行(机器解析), 默认纯文本结构化")
    args = parser.parse_args()
    log_file = args.log_file or os.environ.get("AI_DAILY_LOG_FILE", "")
    _set_up_logging(level=args.log_level, log_file=log_file)

    B.init_db()
    LOG.info("数据层初始化完成 DB=%s", B.DB_PATH)
    # 首次启动确保有数据
    if not B.list_days():
        LOG.info("首次启动, 触发初始化抓取")
        by_day, _ = B.fetch_all(min_days=MIN_DAYS)
        for d, papers in by_day.items():
            B.save_day(d, papers)
            LOG.info("初始化: 写入 %s -> %s 条", d, len(papers))
    LOG.info("AI 每日摘要 Web 服务器 启动于 http://%s:%s", HOST, PORT)
    LOG.info("源: %s | 数据: %s | 日志: %s", list(B.ARXIV_CATEGORIES), B.DB_PATH,
             log_file if log_file else "console")
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer((HOST, PORT), AppServer)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        LOG.info("收到 Ctrl+C, 正在关闭…")
        try:
            srv.shutdown()   # 停止接受新连接
            srv.server_close()  # 关闭监听 socket
        except Exception:
            pass
        LOG.info("已关闭")


if __name__ == "__main__":
    main()

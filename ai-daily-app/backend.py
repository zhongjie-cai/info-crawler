#!/usr/bin/env python3
"""AI 每日摘要 — 数据层 + 多源抓取。

设计:
- 数据按天独立保存: 一张表 papers(id, day, unique_id, title, summary, url,
  category, category_label, pubDate, source), day 为 YYYY-MM-DD。
- 同一天覆盖更新: 抓取时按 day 先删后插, 保证同一 day 的快照总是最新一次
  抓取结果, 不累积历史。
- 源: arXiv RSS + 官方/新闻 RSS + WordPress WP API + GitHub Atom + HuggingFace HTML。
  全部用纯 stdlib 解析, 无鉴权, 应用独立可运行。 类别可配置。

数据库: 本地 SQLite, 文件 db/papers.db, 同一天 = 一个 day 分区, 覆盖更新。
"""
from __future__ import annotations
import os, re, sqlite3, xml.etree.ElementTree as ET
import urllib.request, urllib.error, json
import email.utils
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db", "papers.db")
os.makedirs(os.path.join(BASE_DIR, "db"), exist_ok=True)

BACKFILL_DAYS = int(os.environ.get("AI_DAILY_BACKFILL_DAYS", "14"))  # 长窗口补完默认天数
UA = {
    "User-Agent": "hermes-agent/1.0 (ai-daily-app; +https://github.com/NousResearch/hermes-agent)",
}


def _get_hf_abstract(url: str) -> str:
    """从 HF 详情页提取正文摘要。
    列表页卡片无独立摘要字段, 详情页有真实正文, 用 og:title 定位后取正文。"""
    data = _fetch(url, timeout=20)
    if not data:
        return ""
    m = re.search(r'og:title[^>]*content="([^"]*)"', data)
    if not m:
        return ""
    rest = data[m.end():]
    # 从 og:title 之后开始找正文 p, 跳过 nav/author/元信息 p, 取前几段
    p_tags = re.findall(r'<p[^>]*>(.*?)</p>', rest, re.DOTALL)
    body = []
    for p in p_tags:
        clean = re.sub(r'<[^>]+>', ' ', p).strip()
        if not clean:
            continue
        if "Models Datasets Spaces" in clean:
            continue  # nav
        if "Published" in clean and "Community Article" in clean:
            continue  # 元信息
        # 跳过短元信息/作者 p
        if len(clean) < 40:
            continue
        body.append(clean)
    if not body:
        return ""
    text = re.sub(r"\s+", " ", " ".join(body[:2]))
    return text[:280] + "…" if len(text) > 280 else text

# 每个类别有: label(显示名), fetch 函数签名
ARXIV_CATEGORIES = {
    "cs.CL":  "计算与语言(自然语言/LLM)",
    "cs.AI":  "人工智能",
    "cs.LG":  "机器学习",
    "cs.SE":  "软件工程(AI 研发/编码)",
    "cs.CR":  "安全",
}

# 非 arXiv 源的类别: 标签 -> 源配置
#   category_label: 显示给用户的类别名
#   type: "rss" | "wp_json" | "atom" | "html"
#   url: 源地址
#   date_field: 日期解析类型
#   (标题/链接/摘要/日期 的字段名按 type 自动解析)
NON_ARXIV_SOURCES = {
    "OpenAI 官方": {
        "type": "rss",
        "url": "https://openai.com/news/rss.xml",
    },
    "DeepMind 官方": {
        "type": "rss",
        "url": "https://deepmind.google/discover/rss/",
    },
    "中文新闻(36kr)": {
        "type": "rss",
        "url": "https://www.36kr.com/feed",
    },
    "科技新闻(TechCrunch)": {
        "type": "wp_json",
        "url": "https://techcrunch.com/wp-json/wp/v2/posts",
        "params": {"category": "577047203", "per_page": "20"},
    },
    "模型发布(HuggingFace)": {
        "type": "html",
        "url": "https://huggingface.co/blog",
    },
    "推理/Host 框架(vLLM)": {
        "type": "atom",
        "url": "https://github.com/vllm-project/vllm/releases.atom",
    },
    "Agent 框架(LangChain)": {
        "type": "atom",
        "url": "https://github.com/langchain-ai/langchain/releases.atom",
    },
    "Agent 框架(LangGraph)": {
        "type": "atom",
        "url": "https://github.com/langchain-ai/langgraph/releases.atom",
    },
    "MCP 工具链": {
        "type": "atom",
        "url": "https://github.com/modelcontextprotocol/modelcontextprotocol/commits.atom",
    },
}


# ─── 通用工具 ─────────────────────────────────────────────
def _parse_date(value: str) -> str | None:
    """各源统一日期解析。返回 'YYYY-MM-DD'(UTC) 或 None。

    支持: RFC-822(arXiv/OpenAI/DeepMind/36kr), ISO-8601(TechCrunch/GitHub),
    以及相对日期(HuggingFace "X days ago")。
    """
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()

    # 1) RFC-822: 'Sat, 24 Oct 2026 00:00:00 -0400'
    m = re.match(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", value)
    if m:
        try:
            dt = email.utils.parsedate_to_datetime(value)
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    # 2) ISO-8601: '2026-10-24T07:15:00' 或 '2026-10-24T...Z'
    m = re.match(r"\d{4}-\d{2}-\d{2}", value)
    if m:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    # 2b) 带时区的 datetime: '2026-10-24 18:50:23 +0800' / '2026-10-24 18:50:23'
    #   36kr 用这种格式。
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\s*([+-]\d{4})?", value)
    if m:
        tz = m.group(7) or "+00:00"
        if tz.startswith("+") or tz.startswith("-"):
            tz = tz[:5] + ":" + tz[5:]
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), int(m.group(6)))
            dt = dt.replace(tzinfo=timezone.fromisoformat("1970-01-01" + tz) if False else
                            timezone(timedelta(hours=int(tz[1:3]) * (1 if tz[0] == "+" else -1),
                                               minutes=int(tz[4:6]) * (1 if tz[0] == "+" else -1))))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    # 3) 相对日期: '3 days ago' / '1 day ago' / '9 days ago'
    m = re.match(r"(\d+)\s+(day|week|hour)s??\s+ago", value, re.I)
    if m:
        n = int(m.group(1))
        today = datetime.now(timezone.utc)
        if m.group(2) == "hour":
            dt = today - timedelta(hours=n)
        elif m.group(2) == "day":
            dt = today - timedelta(days=n)
        else:  # week
            dt = today - timedelta(weeks=n)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

    return None


def _clean_text(value: str) -> str:
    """压缩空白、去首尾。"""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _strip_html(value: str) -> str:
    """去 HTML 标签, 压缩空白。"""
    if not value:
        return ""
    value = re.sub(r"<br[^>]*>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value, flags=re.I)
    return re.sub(r"&nbsp;|&#160;|&#8220;|&#8221;|&#8221;", " ", value) \
           .replace("&amp;", "&").replace("&quot;", '"')
    return _clean_text(re.sub(r"&nbsp;|&#160;", " ", value, flags=re.I))


def _extract_abstract(summary: str) -> str:
    """arXiv description: 'arXiv:xxx Announce Type: new\\nAbstract: ...' 取 Abstract 段。"""
    if not summary:
        return ""
    m = re.search(r"Abstract:\s*(.*)", summary, re.DOTALL)
    if m:
        return _clean_text(m.group(1))
    return _clean_text(summary)


def _short_summary(value: str, max_len: int = 200) -> str:
    """取前 max_len 字符, 去 HTML。用于无完整摘要的源。"""
    v = _strip_html(value)
    if len(v) <= max_len:
        return v
    return v[:max_len].rstrip() + "…"


# ─── 通用下载 ─────────────────────────────────────────────
def _fetch(url: str, timeout: int = 20) -> str:
    """下载并返回文本, 失败返回 ''(不抛)。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return ""


# ─── 各源抓取函数 ─────────────────────────────────────────
def fetch_arxiv(category: str, limit: int = 200) -> list[dict]:
    """抓单个 arXiv 类别 RSS。失败返回 [](不抛)。"""
    url = f"https://rss.arxiv.org/rss/{category}"
    data = _fetch(url, timeout=30)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
        channel = root.find("channel")
        if channel is None:
            return []
    except Exception:
        return []
    items = channel.findall("item")
    out = []
    for e in items:
        if e is None:
            continue
        title = e.find("title")
        link = e.find("link")
        pub = e.find("pubDate")
        desc = e.find("description")
        if title is None or link is None:
            continue
        day = _parse_date(pub.text if pub is not None else "")
        if not day:
            continue
        out.append({
            "unique_id": link.text.strip(),
            "title": _clean_text(title.text),
            "summary": _extract_abstract(desc.text if desc is not None else ""),
            "url": link.text.strip(),
            "category": category,
            "category_label": ARXIV_CATEGORIES.get(category, category),
            "pubDate": (pub.text if pub is not None else "") or day,
            "day": day,
            "source": "arXiv",
        })
        if len(out) >= limit:
            break
    return out


def _rss_items(data: str) -> list[dict]:
    """解析通用 RSS, 返回 item 原始 dict 列表。"""
    if not data:
        return []
    try:
        root = ET.fromstring(data)
        # 兼容 <rss> 和 <channel>; 也兼容 <feed> atom
        if root.tag == "feed":
            return []  # atom 用单独函数
        channel = root.find("channel")
        if channel is None:
            return []
    except Exception:
        return []
    items = []
    for e in channel.findall("item"):
        if e is None:
            continue
        rec = {}
        for tag in ("title", "link", "pubDate", "description", "category", "guid"):
            el = e.find(tag)
            if el is not None and el.text:
                rec[tag] = _clean_text(el.text)
        if rec.get("title") and rec.get("link"):
            items.append(rec)
    return items


def _rss_fetch(label: str, url: str, limit: int = 30) -> list[dict]:
    """通用 RSS 源抓取, 返回 paper dict 列表。失败返回 []。"""
    data = _fetch(url, timeout=20)
    items = _rss_items(data)
    out = []
    for it in items:
        day = _parse_date(it.get("pubDate", ""))
        if not day:
            continue
        out.append({
            "unique_id": it.get("url", "") or it.get("link", ""),
            "title": it.get("title", ""),
            "summary": _short_summary(it.get("description", "")),
            "url": it.get("link", ""),
            "category": label,
            "category_label": label,
            "pubDate": it.get("pubDate", ""),
            "day": day,
            "source": label,
        })
        if len(out) >= limit:
            break
    return out


def fetch_techcrunch(limit: int = 20) -> list[dict]:
    """TechCrunch AI 频道, 用 WordPress WP JSON API。失败返回 []。"""
    url = "https://techcrunch.com/wp-json/wp/v2/posts?category=577047203&per_page={}".format(limit)
    data = _fetch(url, timeout=20)
    if not data:
        return []
    try:
        posts = json.loads(data)
    except Exception:
        return []
    out = []
    for p in posts:
        title = p.get("title", {}).get("rendered", "")
        link = p.get("link", "")
        date = p.get("day", "") or p.get("date", "")
        day = _parse_date(date)
        if not day or not link:
            continue
        # 用 url 作为唯一键
        out.append({
            "unique_id": link,
            "title": _clean_text(title),
            "summary": _short_summary(p.get("excerpt", {}).get("rendered", "")),
            "url": link,
            "category": "科技新闻(TechCrunch)",
            "category_label": "科技新闻(TechCrunch)",
            "pubDate": date,
            "day": day,
            "source": "TechCrunch",
        })
        if len(out) >= limit:
            break
    return out


def fetch_huggingface(limit: int = 10) -> list[dict]:
    """HuggingFace Blog, 解析 HTML 页。列表页卡片只有 title+相对日期+点赞数,
    无独立摘要字段, 摘要需从详情页抓取(正文前几段)。失败返回 []。"""
    data = _fetch("https://huggingface.co/blog", timeout=20)
    if not data:
        return []
    base = "https://huggingface.co"
    out = []
    for art in re.findall(r'<article[^>]*>(.*?)</article>', data, re.DOTALL):
        link_m = re.search(r'<a\s+href="(/blog/[^"]+)"', art)
        if not link_m:
            continue
        title_m = re.search(r'<h4[^>]*>(.*?)</h4>', art)
        if not title_m:
            continue
        title = _clean_text(re.sub(r'<[^>]+>|\[.*?\]', '', title_m.group(1), flags=re.I))
        if not title:
            continue
        # 相对日期
        date_m = re.search(r'(\d+)\s+(day|week|hour)s? ago', art, re.I)
        date_str = ""
        if date_m:
            date_str = "{} {}s ago".format(date_m.group(1), date_m.group(2).lower())
        day = _parse_date(date_str)
        if not day:
            continue
        url = base + link_m.group(1)
        out.append({
            "unique_id": link_m.group(1),
            "title": title,
            "summary": "",
            "url": url,
            "category": "模型发布(HuggingFace)",
            "category_label": "模型发布(HuggingFace)",
            "pubDate": date_str,
            "day": day,
            "source": "HuggingFace",
        })
        if len(out) >= limit:
            break
    # 对每条抓详情页取正文摘要
    for p in out:
        p["summary"] = _get_hf_abstract(p["url"])
    return out


def _atom_fetch(label: str, url: str, limit: int = 15) -> list[dict]:
    """通用 GitHub Atom 源。元素带 Atom namespace, 日期在 <updated> 文本(ISO)。"""
    data = _fetch(url, timeout=20)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
    except Exception:
        return []
    out = []
    for e in root.findall(".//{*}entry"):
        if e is None:
            continue
        title_el = e.find(".//{*}title")
        if title_el is None or not title_el.text:
            continue
        title = _clean_text(title_el.text)
        if not title:
            continue
        # 日期: <updated> 文本(ISO), 如 '2026-10-23T10:50:53Z'
        day = None
        updated = ""
        updated_el = e.find(".//{*}updated")
        if updated_el is not None and updated_el.text:
            updated = updated_el.text.strip()
            day = _parse_date(updated)
        if not day:
            continue
        # 唯一 id + url: 从 <link rel=alternate> 提取
        link = ""
        link_el = e.find(".//{*}link")
        if link_el is not None and link_el.get("rel") == "alternate":
            link = link_el.get("href", "")
        if not link:
            id_el = e.find(".//{*}id")
            if id_el is not None and id_el.text:
                link = re.sub(r"^\S*:", "", id_el.text).strip()
                if not link.startswith("http"):
                    link = "https:" + link
        if not link:
            continue
        out.append({
            "unique_id": link,
            "title": title,
            "summary": "",
            "url": link,
            "category": label,
            "category_label": label,
            "pubDate": updated,
            "day": day,
            "source": "GitHub",
        })
        if len(out) >= limit:
            break
    return out


def fetch_all(min_days: int = 3, mode: str = "backfill", limit: int = 200) -> dict:
    """抓全部配置类别 + 全部非 arXiv 源, 合并去重, 返回 {day: [papers]}。

    两种模式:
    - mode="focus"   短窗口: 只保留近 min_days 天(默认 3), 每日"聚焦"摘要。
    - mode="backfill" 长窗口: 用较大窗口(BACKFILL_DAYS, 默认 14)确保更新不频繁
      的源(如 GitHub release)不被遗漏。默认用长窗口, 保证信息无遗漏。

    同一 url 若跨源被去重则归入它第一次出现的 day。
    返回按 day 分组, 每 day 内按 pubDate 倒序。
    """
    all_items = []
    errors = {}
    # arXiv
    for cat in ARXIV_CATEGORIES:
        res = fetch_arxiv(cat, limit=limit)
        errors[cat] = len(res)
        all_items.extend(res)
    # 非 arXiv
    for label, cfg in NON_ARXIV_SOURCES.items():
        t = cfg["type"]
        if t == "rss":
            res = _rss_fetch(label, cfg["url"])
        elif t == "wp_json":
            res = fetch_techcrunch()
        elif t == "atom":
            res = _atom_fetch(label, cfg["url"])
        elif t == "html":
            res = fetch_huggingface()
        else:
            res = []
        errors[label] = len(res)
        all_items.extend(res)

    # 跨源去重: 按 unique_id (url) 去重, 保持顺序
    seen = set()
    all_items = [x for x in all_items
                 if (x["unique_id"] not in seen and not (seen.add(x["unique_id"])))]

    if not all_items:
        return {}, {k: v for k, v in errors.items()}

    # 窗口: 长窗口默认, 短窗口可切
    window = BACKFILL_DAYS if mode == "backfill" else min_days
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window)
    cutoff_date = cutoff.strftime("%Y-%m-%d")

    by_day = {}
    for it in all_items:
        if it["day"] < cutoff_date:
            continue
        by_day.setdefault(it["day"], []).append(it)
    for day in by_day:
        by_day[day].sort(key=lambda x: x.get("pubDate", ""), reverse=True)
    return by_day, {k: v for k, v in errors.items()}


# ─── 数据库 ───────────────────────────────────────────────
def init_db() -> None:
    """建表(若不存在)。papers 主键(unique_id, day): 同一天同 id 唯一。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                unique_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                category TEXT NOT NULL,
                category_label TEXT NOT NULL,
                pubDate TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'arXiv',
                UNIQUE(unique_id, day)
            )
        """)
        # 兼容旧表: 若存在旧列 arxiv_id, 把数据迁移
        cur = conn.execute("PRAGMA table_info(papers)")
        cols = [r[1] for r in cur.fetchall()]
        if "arxiv_id" in cols and "unique_id" not in cols:
            conn.execute("ALTER TABLE papers RENAME COLUMN arxiv_id TO unique_id")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_day ON papers(day)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title)")
        conn.commit()
    finally:
        conn.close()


def save_day(day: str, papers: list[dict]) -> int:
    """将某一天的 papers 写入数据库, 同一天覆盖更新(先删该 day 再插)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM papers WHERE day = ?", (day,))
        cur = conn.cursor()
        count = 0
        for p in papers:
            cur.execute("""
                INSERT INTO papers (day, unique_id, title, summary, url,
                                    category, category_label, pubDate, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (p["day"], p.get("unique_id", ""), p.get("title", ""),
                  p.get("summary", ""), p.get("url", ""), p.get("category", ""),
                  p.get("category_label", ""), p.get("pubDate", ""),
                  p.get("source", "arXiv")))
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def list_days() -> list[str]:
    """返回数据库中所有 day(去重, 倒序)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT DISTINCT day FROM papers ORDER BY day DESC")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_papers_for_day(day: str) -> list[dict]:
    """取某天的全部论文。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT * FROM papers WHERE day = ? ORDER BY pubDate DESC", (day,))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def count_for_day(day: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM papers WHERE day = ?", (day,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def get_categories() -> list[dict]:
    """返回数据库中所有 (category_label, count) 聚合。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT category_label, COUNT(*) FROM papers "
            "GROUP BY category_label ORDER BY COUNT(*) DESC"
        )
        return [dict(zip(("label", "count"), r)) for r in cur.fetchall()]
    finally:
        conn.close()


def count_by_day_and_cat(days: list[str], cat_label: str) -> dict:
    """返回 {day: 条数}, 按 category_label 筛选。
    cat_label 为 None/空 表示"全部"(不计类别)。用于选中类别后
    日期条的计数跟着筛选变化, 避免筛选后显示旧总数。"""
    if cat_label:
        q = "SELECT day, COUNT(*) FROM papers WHERE category_label = ? GROUP BY day"
        params = (cat_label,)
    else:
        q = "SELECT day, COUNT(*) FROM papers GROUP BY day"
        params = ()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(q, params)
        result = {r[0]: r[1] for r in cur.fetchall()}
        return {d: result.get(d, 0) for d in days}
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    by_day, counts = fetch_all(min_days=3)
    print("抓取完成, 各源返回条数:")
    for k, v in counts.items():
        print(f"  {k}: {v} 条")
    total = sum(len(v) for v in by_day.values())
    print(f"合并去重后共 {total} 条, 按 day(近 3 天):")
    for d, papers in sorted(by_day.items(), reverse=True):
        print(f"  {d}: {len(papers)} 条")
        for p in papers[:3]:
            print(f"    - {p['title'][:60]} ({p['category_label']})")
    for d, papers in by_day.items():
        n = save_day(d, papers)
        print(f"  写入 {d} -> {n} 条")
    if not by_day:
        print("无新数据(正常, 源空)")

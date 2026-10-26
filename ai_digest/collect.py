#!/usr/bin/env python3
"""AI 摘要采集器 — 每日版(确定性底座)。
只跑纯 HTTP 可保证的通道: arXiv(cs.AI/cs.LG) + OpenAI RSS。
解析 -> 去重(state.json) -> 输出 JSON 项。"""
import json, re, os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "digests")
OS_STATE = os.path.join(HERE, "state.json")
SINCE_DAYS = int(os.environ.get("SINCE_DAYS", "2"))
os.makedirs(BASE, exist_ok=True)
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
SINCE = NOW - timedelta(days=SINCE_DAYS)

DETERMINISTIC_FEEDS = [
    {"src": "arXiv cs.AI", "url": "https://rss.arxiv.org/rss/cs.AI"},
    {"src": "arXiv cs.LG", "url": "https://rss.arxiv.org/rss/cs.LG"},
    {"src": "OpenAI News", "url": "https://openai.com/news/rss.xml"},
]

def _tz(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

def _parse_date(s):
    if not s: return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y", "%d %b %Y %H:%M:%S"):
        try: return _tz(datetime.strptime(s, fmt))
        except ValueError: pass
    try: return _tz(datetime.fromisoformat(s.replace("Z","+00:00")))
    except Exception: return None

def load_state():
    if os.path.exists(OS_STATE):
        try: return json.load(open(OS_STATE, encoding="utf-8"))
        except Exception: pass
    return {"seen": {}, "last_run": None}

def save_state(st):
    with open(OS_STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def parse_rss_items(xml):
    """Parse RSS 2.0 <channel><item> or Atom <feed><entry>."""
    root = ET.fromstring(xml)
    if root.tag in ("rss", "channel"):
        channel = root
        if channel.tag != "channel":
            channel = root.find("channel")
        if channel is not None:
            return channel.findall("item")
        return []
    return root.findall("entry")

def get_text(el, *tags):
    for tag in tags:
        n = el.find(tag)
        if n is not None and n.text:
            return n.text.strip()
    return ""

def _rss_entries(xml, src):
    items = parse_rss_items(xml)
    out = []
    for e in items:
        t = get_text(e, "title")
        l = get_text(e, "link")
        d = get_text(e, "pubDate")
        desc = get_text(e, "description")
        if t and l:
            out.append({"title": t, "link": l, "date": d,
                        "summary": desc[:220] + ("…" if len(desc) > 220 else ""),
                        "src": src})
    return out

def _feed(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "hermes-agent/1.0 (ai-digest-deterministic; +https://github.com/NousResearch/hermes-agent)",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()

def main():
    st = load_state()
    fresh = []
    errors = []
    for f in DETERMINISTIC_FEEDS:
        try:
            xml = _feed(f["url"])
        except Exception as e:
            errors.append(f"{f['src']}: {e}")
            continue
        for it in _rss_entries(xml, f["src"]):
            d = _parse_date(it.get("date", ""))
            if d and d >= SINCE:
                fresh.append(it)
    # 去重(同 link 一次) + 过滤已见
    fresh = list(dict((
        re.sub(r"\s+", " ", it["link"].lower()), it
    ) for it in fresh).values())
    fresh = [it for it in fresh
             if re.sub(r"\s+", " ", it["link"].lower()) not in st["seen"]]
    for it in fresh:
        st["seen"][re.sub(r"\s+", " ", it["link"].lower())] = it.get("date") or TODAY
    st["last_run"] = NOW.isoformat()
    save_state(st)
    out = os.path.join(BASE, f"{TODAY}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# AI 摘要(底座) · {TODAY}\n\n")
        f.write(f"*纯 RSS 底座: arXiv + OpenAI。 完整 5 类摘要由 agent 外层补全。*\n\n")
        if fresh:
            f.write("## 新增\n")
            for it in fresh:
                f.write(f"- **{it['title']}** ({it['src']})\n")
                f.write(f"  {it['link']}\n")
                f.write(f"  {it['summary']}\n\n")
        else:
            f.write("*今日无新增(底座)。*\n")
    print(json.dumps({
        "date": TODAY,
        "since": SINCE.isoformat(),
        "fresh_count": len(fresh),
        "fresh": fresh,
        "errors": errors,
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()

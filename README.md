# info-crawler

AI 领域每日信息摘要工具。核心是 `ai-daily-app/` —— 一个**纯 Python 标准库**（`urllib` + `http.server` + `sqlite3`）实现的本地 Web 应用，无需第三方依赖、无需 `npm`、无需 Flask。它从 16 个 AI 相关源抓取每日信息，按天独立存储到 SQLite 数据库，并提供可筛选的网页界面。

> 说明：本仓库同时包含 `ai_digest/`（早期版）与 `ai-daily-app/`（当前主应用）。本 README 主要针对 **`ai-daily-app/`**。

---

## 目录结构

```
info-crawler/
├── ai-daily-app/          # 当前主应用
│   ├── server.py          # Web 服务器（HTTP API + 静态文件）
│   ├── backend.py         # 数据层 + 16 个源抓取逻辑
│   ├── web/
│   │   ├── index.html     # 前端页面
│   │   ├── css.css        # 样式（暗色主题）
│   │   └── app.js         # 前端逻辑（纯 vanilla JS）
│   └── db/
│       └── papers.db      # SQLite 数据库（按天存储抓取数据）
│   └── __pycache__/       # 字节码缓存（已 gitignore）
├── ai_digest/             # 早期版（保留）
├── .gitignore
├── LICENSE
└── README.md
```

---

## 数据源（16 个）

抓取源在 `backend.py` 的 `ARXIV_CATEGORIES` 与 `NON_ARXIV_SOURCES` 配置。

### arXiv（学术模型，5 个类别）
| 类别 | 说明 |
|---|---|
| cs.CL | 计算与语言（自然语言 / LLM） |
| cs.AI | 人工智能 |
| cs.LG | 机器学习 |
| cs.SE | 软件工程（AI 研发 / 编码） |
| cs.CR | 安全 |

### 非 arXiv 源（11 个）
| 类别 | 源 | 类型 | 来源 URL |
|---|---|---|---|
| OpenAI 官方 | OpenAI | RSS | `openai.com/news/rss.xml` |
| DeepMind 官方 | DeepMind | RSS | `deepmind.google/discover/rss/` |
| 中文新闻 (36kr) | 36 氪 | RSS | `36kr.com/feed` |
| 科技新闻 (TechCrunch) | TechCrunch AI | WordPress API | `techcrunch.com/wp-json/wp/v2/posts` |
| 模型发布 (HuggingFace) | HF Blog | HTML | `huggingface.co/blog` |
| 推理 / Host 框架 (vLLM) | vLLM | GitHub Atom | `vllm-project/vllm/releases.atom` |
| Agent 框架 (LangChain) | LangChain | GitHub Atom | `langchain-ai/langchain/releases.atom` |
| Agent 框架 (LangGraph) | LangGraph | GitHub Atom | `langchain-ai/langgraph/releases.atom` |
| MCP 工具链 | MCP | GitHub Atom | `modelcontextprotocol/modelcontextprotocol/commits.atom` |
| InfoQ 开源技术 | InfoQ 中文 | RSS | `infoq.cn/feed/` |
| 开源(GitHub Trending) | GitHub Trending | HTML | `github.com/trending` |

> 各源抓取失败时**优雅跳过**，不影响整体。源可配置于 `backend.py`。数据入库条数随抓取时点变化（当前已含 InfoQ 与 GitHub Trending）。

---

## 运行方式

### 1. 环境要求
- **Python 3.8+**（本项目在 Python 3.12 验证；`urllib` / `http.server` / `sqlite3` 均为标准库）
- 无第三方依赖，无需 `pip install`、无需 `npm`
- 需能访问各源的网络（部分源需 VPN，见"维护"）

### 2. 启动
```bash
cd ai-daily-app
AI_DAILY_PORT=8787 python3 server.py
```

打开浏览器访问：**`http://127.0.0.1:8787`**

### 3. 端口与环境变量
| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_DAILY_PORT` | 8888 | Web 服务器端口（示例用 8787） |
| `AI_DAILY_HOST` | 127.0.0.1 | 监听地址 |
| `AI_DAILY_MIN_DAYS` | 3 | 短窗口（focus）的天数 |
| `AI_DAILY_DEFAULT_MODE` | backfill | 默认刷新模式（`backfill` 长窗口 / `focus` 短窗口） |

> `AI_DAILY_DEFAULT_MODE` 控制服务器默认刷新窗口（`?mode=` 可在浏览器端覆盖）。

---

## 功能说明

### 窗口模式（长 vs 短）
- **长窗口（backfill，近 14 天）**：确保信息无遗漏，GitHub release 等更新不频繁的源也不被漏掉。
- **短窗口（focus，近 3 天）**：每日聚焦，只保留最近几天。

切换方式：
- **服务器端**：`AI_DAILY_DEFAULT_MODE=focus` 设默认；或浏览器刷新时 POST `/api/refresh?mode=focus`。
- **浏览器端**：页头右侧「短 / 长」按钮，刷新时按所选模式。

### 类别筛选
左侧列表可点击类别筛选当日论文；点"全部"恢复。

### 类别计数逻辑（按总量 / 按当天）
类别标题右侧的 **checkbox「按总量」** 控制计数方式，默认**按当天**：
- **不勾选（默认）**：按当天 —— 当前选中那天的论文，按类别统计，只列今天出现的类别（按计数倒序）。"全部"= 当天总篇数。
- **勾选**：按总量 —— 全库所有 day 的类别聚合。"全部"= 全库总条数。

### 摘要
- arXiv / RSS 源：自带摘要。
- HuggingFace：列表页无独立摘要，应用自动抓**详情页正文前几段**作为摘要。
- GitHub Atom 源（vLLM 等）：无正文摘要（`summary` 为空），展示版本标题。

### 数据覆盖
**同一天覆盖更新**：`save_day()` 先 `DELETE` 后 `INSERT`，重跑同一天不会累积。

---

## API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 前端页面 |
| GET | `/api/days` | 所有 day（倒序） |
| GET | `/api/days/{day}` | 某天的论文 |
| GET | `/api/days/count` | 按类别的每日计数（`?cat=`） |
| GET | `/api/categories` | 类别聚合（全库） |
| GET | `/api/stats` | 全局统计 + 窗口配置 |
| GET | `/api/health` | 健康检查 |
| POST | `/api/refresh` | 触发抓取（`?mode=focus\|backfill`） |

示例：
```bash
curl http://127.0.0.1:8787/api/health
curl http://127.0.0.1:8787/api/stats
curl -X POST "http://127.0.0.1:8787/api/refresh?mode=backfill"
```

---

## 数据表结构（SQLite）

`db/papers.db` 中 `papers` 表：

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| day | TEXT | 日期（`YYYY-MM-DD`） |
| unique_id | TEXT | 唯一标识 |
| title | TEXT | 标题 |
| summary | TEXT | 摘要 |
| url | TEXT | 原文链接 |
| category | TEXT | arXiv 类别键 |
| category_label | TEXT | 类别显示名（筛选用） |
| pubDate | TEXT | 发布时间 |
| source | TEXT | 来源（默认 `arXiv`） |

---

## 维护说明

### 抓取
- 点击浏览器「刷新数据」按钮，或 `POST /api/refresh`。
- 首跑会自动拉取并入库；服务器启动时若库空也会自动初始化一次。
- 每源抓取锁（`_refresh_lock`）防止并发刷新。
- 抓取超时 / 网络失败时各源优雅跳过，返回已抓到的部分。

### 添加 / 修改数据源
在 `backend.py` 中修改：
1. **arXiv 类别**：`ARXIV_CATEGORIES` 字典（键为类别名，值为显示名）。
2. **非 arXiv 源**：`NON_ARXIV_SOURCES` 列表，每项含：
   ```python
   {
       "label": "显示名",
       "type": "rss" | "wp_json" | "atom" | "html" | "html_trending",
       "url": "源 URL",
       # html 类型: HuggingFace 需抓详情页取正文
       # html_trending: GitHub Trending 式 HTML(无独立解析器, 用 fetch_github_trending)
   }
   ```
3. 不同 `type` 用对应解析器（`_rss_fetch` / `fetch_techcrunch` / `_atom_fetch` / `fetch_huggingface` / `fetch_github_trending`）。
4. 修改后重启 `server.py` 并点"刷新数据"验证。

### 窗口 / 天数调参
- 短窗口天数：`AI_DAILY_MIN_DAYS`
- 长窗口天数：`backend.py` 中 `BACKFILL_DAYS`（默认 14）
- 默认模式：`AI_DAILY_DEFAULT_MODE`（`backfill` / `focus`）

### 数据库
- 数据库文件 `db/papers.db`，可手动删除或备份。删除后首跑会重新抓取。
- 查看数据：
  ```bash
  sqlite3 db/papers.db "SELECT day, COUNT(*) FROM papers GROUP BY day ORDER BY day;"
  ```

### 常见故障排查
| 现象 | 原因 / 解决 |
|---|---|
| 部分源返回 0 条 | 网络 / 偶发超时 / 源更新不频繁。重跑或开 VPN；GitHub release 源偶发漏进窗口，用长窗口可补 |
| 端口占用 | `AI_DAILY_PORT` 改到其他端口 |
| 类别"全部"计数异常 | 前端 `app.js` 的计数逻辑；默认按当天，勾选"按总量"用全库 |
| 抓不到 HuggingFace 摘要 | HF 列表页无摘要，应用自动抓详情页正文；需能访问 `huggingface.co/blog` 详情页 |

---

## 快速验证

```bash
cd ai-daily-app
# 1. 健康检查
curl http://127.0.0.1:8787/api/health
# 2. 统计
curl http://127.0.0.1:8787/api/stats
# 3. 类别
curl http://127.0.0.1:8787/api/categories
# 4. 启动 Web 后浏览器访问 http://127.0.0.1:8787
```

---

## 许可证

见 [`LICENSE`](LICENSE)。

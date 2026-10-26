# 每日 AI 摘要任务 — 说明
# cron 是 fresh session(无当前 context), prompt 必须自包含。
# 每天运行:
# 1) 运行底座 collect.py (arXiv cs.AI/cs.LG + OpenAI RSS) -> state.json 去重 + 底座项
# 2) web_search 5 类(见下方 QUERY)
# 3) web_extract 5 个厂商新闻页(Anthropic/OpenAI/Google/Mistral/DeepMind)
# 4) 汇总 -> 去重 -> 写 digest 文件 -> 最终输出简洁摘要(送 WebUI)
# 工作目录: /workspace/my-crawler/ai_digest
# 状态: state.json; 输出: digests/YYYY-MM-DD.md
# 用户要"按需浏览", 所以最终 response 必须是简洁的索引(每条一行+链接+一句要点), 无冗余叙述。

QUERY = {
 "模型发布": "AI 新模型 发布 2026 open source LLM release",
 "agent 框架": "AI agent framework 新 发布 2026 MCP 工具",
 "inferencing": "LLM 推理 serving vLLM 部署 2026",
 "MCP 网关": "MCP 网关 agent 网关 安全 2026",
 "AI 研发": "AI 编码 agent 软件 研发 流程 2026",
}
VENDOR = [
 "https://www.anthropic.com/news",
 "https://openai.com/blog",
 "https://blog.google/",
 "https://www.mistral.ai/news/",
 "https://deepmind.google/news",
]

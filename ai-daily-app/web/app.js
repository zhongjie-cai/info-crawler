// AI 每日摘要 — 前端 (纯 vanilla JS)
const API = "/api";
const el = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"']/g,
  m => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;",
    "'":"&#39;" }[m]));
const trunc = (s, n) => (s || "").length > n ? s.slice(0, n) + "…" : (s || "");

let state = {
  days: [],          // day 列表
  papers: [],        // 当前 day 论文(未筛选)
  categories: [],    // 类别聚合
  activeDay: null,
  filterCat: null,   // null=全部
  dayNavCounts: {},  // 日期条要显示的计数: 无筛选=总数, 有筛选=筛选后
  mode: "backfill",  // focus=短窗口(近3天), backfill=长窗口(近14天)
  catTotalMode: false, // 类别计数按全库总量显示; false=按当天(默认)
  keyword: [],      // dayBar 关键字数组(AND 过滤: 任一关键字在 标题/摘要/类别 中出现即可)
};

// ---------- 工具 ----------
function toast(msg, cls) {
  let t = document.createElement("div");
  t.className = "toast " + (cls || "");
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); t.remove(); }, 2200);
}

function fmtDate(day) {
  const [y, m, d] = day.split("-");
  const dt = new Date(y, +m - 1, +d);
  const wd = ["一", "二", "三", "四", "五", "六", "日"][dt.getDay()];
  return `${dt.getMonth() + 1}/${dt.getDate()}-${wd}`;
}

function relTime(ts) {
  if (!ts) return "";
  const now = new Date();
  const t = ts;
  const d = (now - t) / 1000;
  if (d < 60) return "刚刚";
  if (d < 3600) return Math.floor(d / 60) + " 分钟前";
  if (d < 86400) return Math.floor(d / 3600) + " 小时前";
  return Math.floor(d / 86400) + " 天前";
}

// ---------- 渲染 ----------
function renderStats() {
  el("statDays").textContent = state.days.length + " 天";
  const catEl = el("catCount");
  if (catEl) catEl.textContent = state.categories.length + " 类别";
}

function renderDayNav() {
  const wrap = el("dayNav");
  const cntEl = document.querySelector(".day-count"); // .day-count 是 class, 用 querySelector
  if (cntEl) cntEl.textContent = state.days.length + " 个日期";
  wrap.innerHTML = "";
  // 日期倒序显示: 最新日期在最左边(state.days 已倒序)
  const nMap = state.dayNavCounts || {};
  state.days.slice().forEach(d => {
    const n = nMap[d] != null ? nMap[d] : (state.paperCounts || {})[d] || 0;
    const chip = document.createElement("div");
    chip.className = "day-chip" + (d === state.activeDay ? " active" : "");
    chip.innerHTML = `\n      <div>${fmtDate(d)}</div>\n      <div class="n">${n} 篇</div>\n    `;
    chip.onclick = () => loadDay(d);
    wrap.appendChild(chip);
  });
}

function renderCatList() {
  const wrap = el("catList");
  wrap.innerHTML = "";
  // 始终显示全库所有类别(state.categories), 切换日期后不会缺类别。
  // 计数: 按总量用全库 count; 按当天用当天(state.papers)该类别条数。
  const cats = state.categories || [];
  let allCount;
  let catList = [];
  if (state.catTotalMode) {
    // 按总量: 全库
    allCount = cats.reduce((a, c) => a + (c.count || 0), 0);
    catList = cats.map(c => ({ label: c.label, count: c.count }));
  } else {
    // 按当天: 全库类别, count 用当天(state.papers)统计, 没数据的类别为0
    allCount = (state.papers || []).length;
    const map = {};
    (state.papers || []).forEach(p => { map[p.category_label] = (map[p.category_label] || 0) + 1; });
    catList = cats.map(c => ({ label: c.label, count: map[c.label] || 0 }))
                  .sort((a, b) => b.count - a.count);
  }
  const all = { label: "全部", count: allCount };
  let node = makeCatNode(all.label, all.count, state.filterCat === null);
  node.onclick = () => { state.filterCat = null; applyFilter(); };
  wrap.appendChild(node);
  catList.forEach(c => {
    const node = makeCatNode(c.label, c.count, state.filterCat === c.label);
    node.onclick = () => { state.filterCat = c.label; applyFilter(); };
    wrap.appendChild(node);
  });
}

// 选中/取消类别后: 重新渲染论文 + 日期条计数(按筛选后) + 类别高亮
async function applyFilter() {
  renderCatList();            // 类别高亮更新
  if (state.filterCat) {
    // 有筛选: 日期条计数 = 该类别在每天的条数
    try {
      const r = await fetch(`${API}/days/count?cat=${encodeURIComponent(state.filterCat)}`)
        .then(r2 => r2.json());
      state.dayNavCounts = r.counts || {};
    } catch (e) {
      state.dayNavCounts = {};
    }
  } else {
    // 全部: 日期条计数回到总数
    state.dayNavCounts = {};
  }
  renderDayNav();            // 日期条计数更新
  renderPapers();            // 论文列表更新(已按 filterCat 筛选)
  // 筛选后更新 dayBar 计数, 与列表一致
  updateDayBar();
}

function makeCatNode(label, count, active) {
  const d = document.createElement("div");
  d.className = "cat-item" + (active ? " active" : "");
  d.innerHTML = `<span><b>${esc(label)}</b></span><span class="n">${count}</span>`;
  return d;
}

// ---------- dayBar 关键字过滤 ----------
// 判断一条 paper 是否匹配所有关键字(AND):
// 每个关键字必须都在 (标题 标题+摘要 类别) 中至少一处出现。
// 空关键字忽略。
function paperMatches(p) {
  if (!state.keyword.length) return true;
  const hay = ((p.title || "") + " " + (p.summary || "") + " " +
               (p.category_label || "") + " " + (p.source || ""))
              .toLowerCase();
  return state.keyword.every(k => hay.includes(k.toLowerCase()));
}

// 把某 day 下匹配关键字的论文数, 用于 dayBar 计数(客户端算, 无需新 API)
function dayPaperCount(day, papers) {
  return papers.filter(p => p.day === day && paperMatches(p)).length;
}

function renderKwLabels() {
  const wrap = el("kwLabels");
  if (!wrap) return;
  wrap.innerHTML = "";
  state.keyword.forEach((kw, i) => {
    const lab = document.createElement("span");
    lab.className = "kw-label";
    lab.innerHTML = `<b>${esc(kw)}</b><span class="x">×</span>`;
    lab.onclick = () => {
      state.keyword.splice(i, 1);
      renderKwLabels();
      updateDayBar();
      renderPapers();
      toast(`已移除关键字「${kw}」`, "ok");
    };
    wrap.appendChild(lab);
  });
}

// 添加关键字(回车)
function addKeyword() {
  const inp = el("kwInput");
  if (!inp) return;
  const v = (inp.value || "").trim();
  if (!v) return;
  // 去重(大小写不敏感)
  const exists = state.keyword.some(k => k.toLowerCase() === v.toLowerCase());
  if (exists) { toast("关键字「" + v + "」已存在", "ok"); return; }
  state.keyword.push(v);
  inp.value = "";
  renderKwLabels();
  updateDayBar();
  renderPapers();
  toast(`已添加关键字「${v}」· 共 ${state.keyword.length} 个`, "ok");
}

// 清空全部关键字
function clearKeywords() {
  state.keyword = [];
  renderKwLabels();
  updateDayBar();
  renderPapers();
  toast("已清空全部关键字", "ok");
}

// 重新计算 dayBar 的计数(类别 + 关键字 双重过滤, 仅当前 day)
function updateDayBar() {
  const db = el("dayBar");
  if (!db) return;
  if (db.style.display === "none") return;
  let rows = state.papers;
  if (state.filterCat) rows = rows.filter(p => p.category_label === state.filterCat);
  rows = rows.filter(p => paperMatches(p));
  // 更新 dayBar 计数
  const cntEl = el("dayBarMain") ? el("dayBarMain").querySelector(".cnt") : null;
  if (cntEl) {
    let suffix = "";
    if (state.filterCat) suffix += "「" + state.filterCat + "」";
    if (state.keyword.length) {
      suffix += " 关键字「" + state.keyword.join("/") + "」";
    }
    cntEl.textContent = rows.length + " 篇" + (suffix ? " " + suffix : "");
  }
}

function renderPapers() {
  const wrap = el("paperList");
  const empty = el("emptyState");
  let rows = state.filterCat
    ? state.papers.filter(p => p.category_label === state.filterCat)
    : state.papers;
  // 关键字 AND 过滤(标题+摘要+类别+来源)
  rows = rows.filter(p => paperMatches(p));
  if (rows.length === 0) {
    wrap.innerHTML = "";
    empty.style.display = "block";
    if (state.filterCat) {
      const kws = state.keyword.length ? " 关键字「" + state.keyword.join("/") + "」" : "";
      empty.textContent = `「${state.filterCat}」${kws} 今天暂无数据`;
    } else if (state.keyword.length) {
      empty.textContent = `关键字「${state.keyword.join("/")}」今天暂无匹配`;
    } else {
      empty.textContent = "暂无数据 · 点击右上角 刷新数据";
    }
    return;
  }
  empty.style.display = "none";
  wrap.innerHTML = "";
  rows.forEach(p => {
    const card = document.createElement("div");
    card.className = "paper";
    card.innerHTML = `\n      <div class="p-top">\n        <div class="p-title">${esc(p.title)}</div>\n        <div class="p-meta">\n          <div class="cat">${esc(p.category_label)}</div>\n          <div class="date">${fmtDate(p.day)}</div>\n        </div>\n      </div>\n      <div class="p-sum">${esc(p.summary || "无摘要")}</div>\n      <a class="p-url" target="_blank" rel="noopener" href="${esc(p.url)}">\n        <span class="lbl">→ 打开原文</span> ${esc(trunc(p.url, 46))}\n      </a>\n    `;
    card.querySelector(".p-sum").onclick = e => {
      e.stopPropagation();
      const s = e.target;
      s.classList.toggle("expanded");
    };
    wrap.appendChild(card);
  });
  // 更新 dayBar 计数, 与列表一致
  updateDayBar();
}

// ---------- 数据加载 ----------
async function loadAll() {
  const [daysRes, catRes, statRes] = await Promise.all([
    fetch(`${API}/days`).then(r => r.json()),
    fetch(`${API}/categories`).then(r => r.json()),
    fetch(`${API}/stats`).then(r => r.json()),
  ]);
  state.days = daysRes.days || [];
  state.categories = catRes.categories || [];
  state.papers = [];
  // 预计算每个 day 的 paper 数(用于 chip 显示)
  state.paperCounts = {};
  for (const d of state.days) {
    const r = await fetch(`${API}/days/${d}`).then(r2 => r2.json());
    state.paperCounts[d] = r.count || 0;
  }
  if (state.days.length === 0) {
    renderStats();
    renderCatList();
    renderDayNav();
    renderPapers();
    toast("暂无数据，点 刷新数据 抓取", "err");
    return;
  }
  // 先加载最新一天, 再渲染类别列表(此时 state.papers 已就位, 计数正确)
  state.activeDay = state.days[0];
  await loadDay(state.activeDay);
  // loadDay 里已 renderCatList? 否, 这里统一渲染
  renderCatList();
  renderPapers();
  toast(`已加载 ${state.activeDay}，${state.papers.length} 篇`, "ok");
}

async function loadDay(day) {
  state.activeDay = day;
  const r = await fetch(`${API}/days/${day}`).then(r2 => r2.json());
  state.papers = r.papers || [];
  renderStats();
  renderDayNav();
  renderCatList();   // 计数按当天(state.papers)刷新, 切日期后类别不会缺
  renderPapers();
  el("dayBar").style.display = "flex";
  // 只更新 dayBarMain 内容, 保留右侧 kw-filter 输入框
  const main = el("dayBarMain");
  if (main) {
    main.innerHTML = `
      <span class="date">${fmtDate(day)}</span>
      <span class="cnt"></span>
      <span class="time">按发表日期倒序</span>
    `;
  }
  updateDayBar();
}

async function refreshData() {
  const btn = el("btnRefresh");
  btn.classList.add("busy");
  btn.textContent = "";
  btn.innerHTML = `<span class="icon">↻</span> 正在抓取…`;
  try {
    const modeLabel = state.mode === "focus" ? "短窗口(近3天)" : "长窗口(近14天)";
    toast("开始抓取 · " + modeLabel + "…", "ok");
    const r = await fetch(`${API}/refresh?mode=${encodeURIComponent(state.mode)}`, { method: "POST" })
      .then(r2 => r2.json());
    if (r.error) throw new Error(r.error);
    toast(`抓取完成 · ${Object.values(r.saved || {}).reduce((a,b)=>a+b,0)} 篇 · 耗时 ${r.elapsed_s || 0}s`, "ok");
    await loadAll();
    toast(`已更新到 ${state.activeDay}，${state.papers.length} 篇`, "ok");
  } catch (e) {
    toast("抓取失败：" + e.message, "err");
    btn.classList.add("err");
  } finally {
    btn.classList.remove("busy", "err");
    btn.innerHTML = `<span class="icon">↻</span> 刷新数据`;
  }
}

// ---------- 窗口模式切换 ----------
function setMode(mode) {
  state.mode = mode;
  const f = el("modeFocus"), b = el("modeBackfill");
  f.classList.toggle("active", mode === "focus");
  b.classList.toggle("active", mode === "backfill");
  el("modeLabel").textContent = mode === "focus" ? "短窗口" : "长窗口";
}
el("modeFocus").addEventListener("click", () => { setMode("focus"); toast("已切到短窗口 · 近3天", "ok"); });
el("modeBackfill").addEventListener("click", () => { setMode("backfill"); toast("已切到长窗口 · 近14天(默认)", "ok"); });
// 默认从 /stats 读 server 默认模式
fetch(`${API}/stats`).then(r => r.json()).then(d => {
  if (d.default_mode) { state.mode = d.default_mode; setMode(state.mode); }
}).catch(() => {});

// ---------- 启动 ----------
window.addEventListener("load", loadAll);
el("btnRefresh").addEventListener("click", refreshData);
// 类别计数 checkbox: 勾选=按全库总量, 取消=按当天(默认)
el("catTotalMode").addEventListener("change", () => {
  state.catTotalMode = el("catTotalMode").checked;
  renderCatList();   // 立即刷新计数
  renderPapers();    // 类别列表变了, 论文筛选不变, 但重新渲染一次保持高亮一致
  toast(state.catTotalMode ? "计数已按全库总量" : "计数已按当天", "ok");
});
// dayBar 关键字: 回车添加; 输入框为空时按 Backspace 清空全部
el("kwInput").addEventListener("keydown", e => {
  if (e.key === "Enter") {
    e.preventDefault();
    addKeyword();
  } else if (e.key === "Backspace" && (el("kwInput").value || "").trim() === "") {
    clearKeywords();
  }
});
// 自动刷新状态提示
setInterval(() => {
  // 可选: 每 30 秒刷新一次(轻量)
}, 30000);

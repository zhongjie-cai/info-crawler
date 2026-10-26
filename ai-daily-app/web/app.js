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
  el("statTotal").textContent = state.papers.length + " 篇";
  const catEl = el("catCount");
  if (catEl) catEl.textContent = state.categories.length + " 类别";
}

function renderDayNav() {
  const wrap = el("dayNav");
  const cntEl = document.querySelector(".day-count"); // .day-count 是 class, 用 querySelector
  if (cntEl) cntEl.textContent = state.days.length + " 个日期";
  wrap.innerHTML = "";
  // 计数用 dayNavCounts(筛选后); 无筛选时 = 总数
  const nMap = state.dayNavCounts || {};
  state.days.slice().reverse().forEach(d => {
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
  // 两种计数逻辑, 由 checkbox 控制
  let allCount, catCounts;
  if (state.catTotalMode) {
    // 按总量: 全库(所有 day)的类别聚合
    const cats = state.categories || [];
    allCount = cats.reduce((a, c) => a + (c.count || 0), 0);
    catCounts = cats.map(c => ({ label: c.label, count: c.count }));
  } else {
    // 按当天: 当前选中那天的论文, 按类别统计
    const dayPapers = state.papers || [];
    allCount = dayPapers.length;
    const map = {};
    dayPapers.forEach(p => { map[p.category_label] = (map[p.category_label] || 0) + 1; });
    const cats = state.categories || [];
    // 只显示今天真正出现的类别(按计数倒序)
    catCounts = cats
      .filter(c => (map[c.label] || 0) > 0)
      .map(c => ({ label: c.label, count: map[c.label] }))
      .sort((a, b) => b.count - a.count);
    // 补充: 若 today 有类别但不在 categories 聚合里(极少), 兜底加入
    Object.keys(map).forEach(lbl => {
      if (!catCounts.some(c => c.label === lbl)) catCounts.push({ label: lbl, count: map[lbl] });
    });
  }
  const all = { label: "全部", count: allCount };
  let node = makeCatNode(all.label, all.count, state.filterCat === null);
  node.onclick = () => { state.filterCat = null; applyFilter(); };
  wrap.appendChild(node);
  catCounts.forEach(c => {
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
  // 顶部"X 篇"
  const cntEl = el("statTotal");
  if (cntEl) cntEl.textContent = (state.filterCat ? 0 : state.papers.length) + " 篇";
}

function makeCatNode(label, count, active) {
  const d = document.createElement("div");
  d.className = "cat-item" + (active ? " active" : "");
  d.innerHTML = `<span><b>${esc(label)}</b></span><span class="n">${count}</span>`;
  return d;
}

function renderPapers() {
  const wrap = el("paperList");
  const empty = el("emptyState");
  let rows = state.filterCat
    ? state.papers.filter(p => p.category_label === state.filterCat)
    : state.papers;
  // 顶部"X 篇" + dayBar 计数都按筛选后的 rows, 与内容一致
  const cntEl = el("statTotal");
  if (cntEl) cntEl.textContent = rows.length + " 篇";
  const db = el("dayBar");
  if (db && db.style.display !== "none") {
    db.innerHTML = `
      <span class="date">${fmtDate(state.activeDay)}</span>
      <span class="cnt">${rows.length} 篇${state.filterCat ? "「" + state.filterCat + "」" : ""}</span>
      <span class="time">按发表日期倒序</span>
    `;
  }
  if (rows.length === 0) {
    wrap.innerHTML = "";
    empty.style.display = "block";
    empty.textContent = state.filterCat
      ? `「${state.filterCat}」今天暂无数据`
      : "暂无数据 · 点击右上角 刷新数据";
    return;
  }
  empty.style.display = "none";
  wrap.innerHTML = "";
  rows.forEach(p => {
    const card = document.createElement("div");
    card.className = "paper";
    card.innerHTML = `
      <div class="p-top">
        <div class="p-title">${esc(p.title)}</div>
        <div class="p-meta">
          <div class="cat">${esc(p.category_label)}</div>
          <div class="date">${fmtDate(p.day)}</div>
        </div>
      </div>
      <div class="p-sum">${esc(p.summary || "无摘要")}</div>
      <a class="p-url" target="_blank" rel="noopener" href="${esc(p.url)}">
        <span class="lbl">→ 打开原文</span> ${esc(trunc(p.url, 46))}
      </a>
    `;
    card.querySelector(".p-sum").onclick = e => {
      e.stopPropagation();
      const s = e.target;
      s.classList.toggle("expanded");
    };
    wrap.appendChild(card);
  });
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
  renderStats();
  renderCatList();
  renderDayNav();
  if (state.days.length === 0) {
    renderPapers();
    toast("暂无数据，点 刷新数据 抓取", "err");
    return;
  }
  state.activeDay = state.days[0];
  loadDay(state.activeDay);
}

async function loadDay(day) {
  state.activeDay = day;
  const r = await fetch(`${API}/days/${day}`).then(r2 => r2.json());
  state.papers = r.papers || [];
  renderStats();
  renderDayNav();
  renderPapers();
  el("dayBar").style.display = "flex";
  el("dayBar").innerHTML = `
    <span class="date">${fmtDate(day)}</span>
    <span class="cnt">${state.papers.length} 篇论文</span>
    <span class="time">arXiv 按发表日期倒序</span>
  `;
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
// 自动刷新状态提示
setInterval(() => {
  // 可选: 每 30 秒刷新一次(轻量)
}, 30000);

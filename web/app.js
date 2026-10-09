"use strict";
/* 주식 후보 모니터: data/latest.json + data/charts/<종목>.json 을 읽어 표·차트로 보여준다. */

const LW = window.LightweightCharts;
const LISTS = { box: "박스 하단권", accum: "매집 흔적" };
const RISK_TXT = { high: "높음", mid: "주의", low: "낮음", none: "없음" };
const RISK_IC = { high: "!", mid: "!", low: "·", none: "✓" };
const RISK_ORD = { none: 0, low: 1, mid: 2, high: 3 };
const STAGE_CLS = { "매수 검토": "buy", "관찰": "watch", "대기": "wait" };
const DEFAULT_FILTERS = { q: "", stage: "all", risk: "all", excl: [], noEarn: false, mcap: "all", minTiming: 0, hideChecked: false };
const OVERLAYS = [
  ["box", "박스·손절선", "--s-yellow"], ["sma20", "20일선", "--s-orange"], ["sma50", "50일선", "--s-violet"],
  ["sma200", "200일선", "--s-green"], ["bb", "볼린저", "--muted"], ["st", "슈퍼트렌드", "--up"],
  ["avwap", "앵커드 VWAP", "--s-magenta"], ["earn", "실적일", "--ink2"],
];
const PANES = [["vol", "거래량"], ["rsi", "RSI"], ["macd", "MACD"], ["stoch", "스토캐스틱 RSI"], ["adx", "ADX"], ["mfi", "MFI"], ["obv", "OBV"]];
const PERIODS = [["3M", 63], ["6M", 126], ["1Y", 252], ["3Y", 756], ["5Y", 1300]];

// ---------------------------------------------------------------- 저장 (브라우저에만)
const store = {
  get(k, d) { try { const v = localStorage.getItem("sw." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("sw." + k, JSON.stringify(v)); } catch { /* 저장 불가 환경 */ } },
};

const S = {
  data: null, tab: "box",
  view: store.get("view", matchMedia("(max-width: 700px)").matches ? { box: "grid", accum: "grid" } : { box: "table", accum: "table" }),
  sort: store.get("sort", { box: { k: "timing", d: -1 }, accum: { k: "timing", d: -1 } }),
  filters: { box: { ...DEFAULT_FILTERS, ...store.get("f.box", {}) }, accum: { ...DEFAULT_FILTERS, ...store.get("f.accum", {}) } },
  ov: { box: true, sma20: false, sma50: true, sma200: true, bb: false, st: false, avwap: false, earn: true, ...store.get("ov", {}) },
  panes: { vol: true, rsi: true, macd: true, stoch: false, adx: false, mfi: false, obv: false, ...store.get("panes", {}) },
  period: store.get("period", "1Y"),
  chk: store.get("chk", {}), memo: store.get("memo", {}),
  charts: new Map(), cur: null, chart: null,
};

// ---------------------------------------------------------------- 형식
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const fin = (v) => v !== null && v !== undefined && Number.isFinite(v);
function pct(v, d = 1, signed = true) {
  if (!fin(v)) return "–";
  const s = (v * 100).toFixed(d) + "%";
  return signed && v > 0 ? "+" + s : s;
}
function pctC(v, d = 1) { return fin(v) ? `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${pct(v, d)}</span>` : "–"; }
function px(v) {
  if (!fin(v)) return "–";
  const d = v >= 100 ? 2 : v >= 10 ? 2 : v >= 1 ? 3 : 4;
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function money(v) {
  if (!fin(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1e12) return "$" + (v / 1e12).toFixed(1) + "T";
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M";
  if (a >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
  return "$" + v.toFixed(0);
}
const num = (v, d = 0) => (fin(v) ? v.toFixed(d) : "–");
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const isChecked = (t) => S.chk[t] === S.data.asof;

function stageBadge(s) { return `<span class="stage ${STAGE_CLS[s] || "wait"}">${s === "매수 검토" ? "✓ " : ""}${esc(s)}</span>`; }
function riskBadge(r, withLabel = true) {
  const top = r.flags && r.flags[0];
  const lbl = r.level === "none" ? "주의 없음" : (top ? top.label : RISK_TXT[r.level]) + (r.flags.length > 1 ? ` 외 ${r.flags.length - 1}` : "");
  return `<span class="risk ${r.level}" title="${esc(r.flags.map((f) => f.label).join(" · "))}"><span class="ic">${RISK_IC[r.level]}</span>${withLabel ? `<span class="lbl">${esc(lbl)}</span>` : ""}</span>`;
}
function meter(v) { return `<span class="meter"><span class="track"><span class="fill" style="width:${Math.max(0, Math.min(100, v))}%"></span></span><b>${num(v)}</b></span>`; }
function tkBadges(r, list) {
  let b = "";
  if (r.new) b += `<span class="badge new">NEW</span>`;
  else if (r.streak > 1) b += `<span class="badge">${r.streak}일째</span>`;
  if (list === "box" && r.in_accum) b += `<span class="badge both" title="매집 흔적 목록에도 있음">매집</span>`;
  if (list === "accum" && r.in_box) b += `<span class="badge both" title="박스 하단권 목록에도 있음">박스</span>`;
  return b;
}

// ---------------------------------------------------------------- 필터·정렬
function filtered(list) {
  const f = S.filters[list];
  const q = f.q.trim().toLowerCase();
  let rows = S.data.lists[list].filter((r) => {
    if (q && !(r.t.toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q) || (r.industry || "").toLowerCase().includes(q))) return false;
    if (f.stage !== "all" && r.stage !== f.stage) return false;
    const lv = r.risk.level;
    if (f.risk === "safe" && RISK_ORD[lv] > 1) return false;
    if (f.risk === "nohigh" && lv === "high") return false;
    if (f.risk === "only" && RISK_ORD[lv] < 2) return false;
    if (f.excl.length && r.risk.flags.some((x) => RISK_ORD[x.level] >= 2 && f.excl.includes(x.cat))) return false;
    if (f.noEarn && fin(r.earn_days) && r.earn_days <= 7) return false;
    const m = r.mcap || 0;
    if (f.mcap === "small" && m >= 3e8) return false;
    if (f.mcap === "mid" && (m < 3e8 || m >= 2e9)) return false;
    if (f.mcap === "large" && m < 2e9) return false;
    if (f.mcap === "min100" && m < 1e8) return false;
    if (r.timing < f.minTiming) return false;
    if (f.hideChecked && isChecked(r.t)) return false;
    return true;
  });
  const { k, d } = S.sort[list];
  const key = {
    timing: (r) => r.timing, score: (r) => r.score, rr: (r) => r.rr, rsi: (r) => r.ind.rsi, risk: (r) => RISK_ORD[r.risk.level] * 10 + r.risk.flags.length,
    mcap: (r) => r.mcap, chg1d: (r) => r.chg1d, ret3m: (r) => r.ret3m, pos: (r) => (r.box ? r.box.pos : r.acc.pos52),
    to_high: (r) => (r.box ? r.box.to_high : null), earn: (r) => r.earn_days ?? 999, bull: (r) => r.bull - r.bear,
    vol: (r) => r.vol_ratio5 ?? r.vol_ratio, t: (r) => r.t,
  }[k] || ((r) => r.timing);
  rows.sort((a, b) => {
    const x = key(a), y = key(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * d;
  });
  return rows;
}

function filterBar(list) {
  const f = S.filters[list];
  const cats = S.data.risk_categories;
  const present = new Set(S.data.lists[list].flatMap((r) => r.risk.flags.filter((x) => RISK_ORD[x.level] >= 2).map((x) => x.cat)));
  const sel = (id, opts, v) => `<select data-f="${id}">${opts.map(([k, t]) => `<option value="${k}"${k === v ? " selected" : ""}>${t}</option>`).join("")}</select>`;
  const active = [f.q && `검색 "${esc(f.q)}"`, f.stage !== "all" && f.stage, f.risk !== "all" && { safe: "주의 없음·낮음만", nohigh: "높음 제외", only: "주의 종목만" }[f.risk],
    f.mcap !== "all" && "시총 필터", f.minTiming && `타이밍 ≥ ${f.minTiming}`, f.noEarn && "실적 임박 제외", f.hideChecked && "확인한 종목 숨김",
    f.excl.length && `위험 ${f.excl.length}종 제외`].filter(Boolean);
  const open = S.fopen ?? !matchMedia("(max-width: 700px)").matches;
  return `<details class="fwrap"${open ? " open" : ""}><summary>필터 · 보기 <span class="fsum">${active.length ? "· " + active.join(" · ") : "· 전체 보기"}</span></summary><div class="filters">
    <input type="search" data-f="q" placeholder="종목·이름·업종 검색" value="${esc(f.q)}">
    <label>단계 ${sel("stage", [["all", "전체"], ["매수 검토", "매수 검토"], ["관찰", "관찰"], ["대기", "대기"]], f.stage)}</label>
    <label>위험 ${sel("risk", [["all", "전체"], ["safe", "주의 없음·낮음만"], ["nohigh", "높음 제외"], ["only", "주의 종목만"]], f.risk)}</label>
    <label>시총 ${sel("mcap", [["all", "전체"], ["small", "$3억 미만 (소형주)"], ["mid", "$3억~$20억"], ["large", "$20억 이상"], ["min100", "$1억 이상"]], f.mcap)}</label>
    <label>타이밍 ≥ <input type="range" min="0" max="90" step="5" data-f="minTiming" value="${f.minTiming}"><b class="num" id="mt-v">${f.minTiming}</b></label>
    <label><input type="checkbox" data-f="noEarn"${f.noEarn ? " checked" : ""}> 실적 7일 이내 제외</label>
    <label><input type="checkbox" data-f="hideChecked"${f.hideChecked ? " checked" : ""}> 오늘 확인한 종목 숨기기</label>
    <span class="grow"></span>
    <span class="seg" data-view>${[["table", "표"], ["grid", "차트 모아보기"]].map(([k, t]) => `<button data-v="${k}" class="${S.view[list] === k ? "on" : ""}">${t}</button>`).join("")}</span>
    ${present.size ? `<div class="chips" style="flex-basis:100%"><span class="note">제외할 위험 (주의·높음):</span>${[...present].map((c) =>
      `<button class="chip${f.excl.includes(c) ? " off" : ""}" data-excl="${c}">${esc(cats[c] || c)}</button>`).join("")}
      ${f.excl.length ? `<button class="chip" data-excl-clear>모두 보기</button>` : ""}</div>` : ""}
  </div></details>`;
}

// ---------------------------------------------------------------- 목록 화면
const COLS = {
  box: [
    ["#", null], ["종목", "t", "l"], ["단계", null, "l"], ["타이밍", "timing"], ["위험", "risk", "l"], ["현재가 · 1일", "chg1d"],
    ["박스 위치", "pos"], ["상단까지", "to_high"], ["손익비", "rr"], ["RSI", "rsi"], ["신호 ▲/▼", "bull"], ["박스 점수", "score"],
    ["실적", "earn"], ["이름 · 업종", null, "l"],
  ],
  accum: [
    ["#", null], ["종목", "t", "l"], ["단계", null, "l"], ["타이밍", "timing"], ["위험", "risk", "l"], ["현재가 · 1일", "chg1d"],
    ["매집 점수", "score"], ["52주 위치", "pos"], ["3개월", "ret3m"], ["거래량 5일/50일", "vol"], ["RSI", "rsi"], ["신호 ▲/▼", "bull"],
    ["시총", "mcap"], ["실적", "earn"], ["이름 · 업종", null, "l"],
  ],
};

function rowCells(list, r, i) {
  const common = [
    `<td class="n">${i + 1}</td>`,
    `<td class="l tk"><b>${esc(r.t)}</b>${tkBadges(r, list)}</td>`,
    `<td class="l">${stageBadge(r.stage)}</td>`,
    `<td>${meter(r.timing)}</td>`,
    `<td class="l">${riskBadge(r.risk)}</td>`,
    `<td class="n">${px(r.price)} ${pctC(r.chg1d)}</td>`,
  ];
  const earn = `<td class="n">${fin(r.earn_days) ? `D-${r.earn_days}` : "–"}</td>`;
  const name = `<td class="l wrap">${esc(r.name)}<br><span class="note">${esc(r.industry || "")}</span></td>`;
  const sig = `<td class="sig"><span class="up">▲${r.bull}</span> / <span class="down">▼${r.bear}</span></td>`;
  if (list === "box") {
    return common.concat([
      `<td class="n">${num(r.box.pos * 100)}%</td>`, `<td class="n">${pctC(r.box.to_high, 0)}</td>`,
      `<td class="n">${num(r.rr, 1)}</td>`, `<td class="n">${num(r.ind.rsi)}</td>`, sig, `<td class="n">${num(r.score)}</td>`, earn, name,
    ]).join("");
  }
  return common.concat([
    `<td class="n">${num(r.score, 2)}</td>`, `<td class="n">${fin(r.acc.pos52) ? num(r.acc.pos52 * 100) + "%" : "–"}</td>`,
    `<td class="n">${pctC(r.ret3m, 0)}</td>`, `<td class="n">${fin(r.vol_ratio5) ? r.vol_ratio5.toFixed(1) + "배" : "–"}</td>`,
    `<td class="n">${num(r.ind.rsi)}</td>`, sig, `<td class="n">${money(r.mcap)}</td>`, earn, name,
  ]).join("");
}

function tableHtml(list, rows) {
  const { k, d } = S.sort[list];
  const head = COLS[list].map(([t, key, cls]) =>
    `<th class="${cls || ""}${key === k ? " sorted" : ""}"${key ? ` data-sort="${key}"` : ""}>${t}${key === k ? `<span class="arr">${d < 0 ? "▼" : "▲"}</span>` : ""}</th>`).join("");
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${isChecked(r.t) ? "checked" : ""}">${rowCells(list, r, i)}</tr>`).join("");
  return `<div class="tbl"><table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLS[list].length}" class="empty">조건에 맞는 종목이 없습니다. 필터를 풀어 보세요.</td></tr>`}</tbody></table></div>`;
}

function sparkSvg(r) {
  const v = (r.spark || []).filter(fin);
  if (v.length < 2) return "<svg></svg>";
  const W = 300, H = 120, P = 4;
  let lo = Math.min(...v), hi = Math.max(...v);
  const lines = [];
  if (r.box) { lines.push(["low", r.box.low], ["high", r.box.high], ["stop", r.stop]); }
  for (const [, y] of lines) { if (y < lo && y > lo * 0.6) lo = y; if (y > hi && y < hi * 1.6) hi = y; }
  const span = hi - lo || 1;
  const X = (i) => P + (i / (v.length - 1)) * (W - 2 * P);
  const Y = (y) => P + (1 - (y - lo) / span) * (H - 2 * P);
  const d = v.map((y, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(y).toFixed(1)}`).join("");
  let extra = "";
  if (r.box) {
    const y1 = Math.max(P, Y(r.box.high)), y0 = Math.min(H - P, Y(r.box.low));
    if (y0 > y1) extra += `<rect x="0" y="${y1}" width="${W}" height="${y0 - y1}" fill="var(--s-yellow)" opacity=".12"/>`;
    for (const [n, y] of lines) {
      const yy = Y(y);
      if (yy >= 0 && yy <= H) extra += `<line x1="0" x2="${W}" y1="${yy}" y2="${yy}" stroke="${n === "stop" ? "var(--critical)" : "var(--s-yellow)"}" stroke-width="1" stroke-dasharray="${n === "stop" ? "3 3" : "0"}" vector-effect="non-scaling-stroke"/>`;
    }
  }
  const last = v[v.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="${esc(r.t)} 최근 1년 종가">${extra}
    <path d="${d}" fill="none" stroke="var(--ink2)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
    <circle cx="${X(v.length - 1)}" cy="${Y(last)}" r="3" fill="var(--accent)"/></svg>`;
}

function gridHtml(list, rows) {
  if (!rows.length) return `<div class="card empty">조건에 맞는 종목이 없습니다.</div>`;
  return `<div class="grid">${rows.map((r, i) => `<div class="gcard${isChecked(r.t) ? " checked" : ""}" data-i="${i}">
    <div class="gh"><b>${esc(r.t)}</b>${tkBadges(r, list)}<span class="grow"></span>${stageBadge(r.stage)}</div>
    <div class="gn">${esc(r.name)} · ${esc(r.industry || "")}</div>
    ${sparkSvg(r)}
    <div class="gf"><span>타이밍 <b>${num(r.timing)}</b></span><span>${px(r.price)} ${pctC(r.chg1d)}</span>${riskBadge(r.risk, false)}</div>
    <div class="gf"><span class="note">${list === "box" ? `박스 위치 ${num(r.box.pos * 100)}% · 상단까지 ${pct(r.box.to_high, 0)}` : `매집 ${num(r.score, 2)} · 3개월 ${pct(r.ret3m, 0)}`}</span>
      <span class="note">RSI ${num(r.ind.rsi)}</span></div>
  </div>`).join("")}</div>`;
}

function renderList(list) {
  const all = S.data.lists[list];
  const rows = filtered(list);
  S.rows = rows;
  const nBuy = all.filter((r) => r.stage === "매수 검토").length;
  const nHigh = all.filter((r) => r.risk.level === "high").length;
  const nNew = all.filter((r) => r.new).length;
  const nChk = all.filter((r) => isChecked(r.t)).length;
  const intro = list === "box"
    ? "3~5년 동안 같은 가격대를 여러 번 오간 종목 중 지금 박스 하단 근처에 있는 종목입니다. 타이밍 점수는 박스 하단 근접·손익비·RSI·MACD·볼린저·스토캐스틱 RSI·슈퍼트렌드를 합친 값입니다."
    : "주가는 별로 안 올랐는데 사는 거래량이 쌓이는 종목입니다 (소형주 포함). 타이밍 점수는 매집 순위·변동성 수축·OBV·거래량 증가·추세 회복·RSI를 합친 값입니다.";
  $("#main").innerHTML = `
    <p class="note" style="margin:2px 0 8px">${intro}</p>
    <div class="tiles">
      <div class="tile"><div class="k">후보</div><div class="v">${all.length}</div></div>
      <div class="tile click" data-quick="buy"><div class="k">매수 검토 (타이밍 60↑, 고위험 아님)</div><div class="v">${nBuy}</div></div>
      <div class="tile click" data-quick="high"><div class="k">매수 주의 (위험 높음)</div><div class="v">${nHigh}</div></div>
      <div class="tile"><div class="k">오늘 새로 들어옴</div><div class="v">${nNew}</div></div>
      <div class="tile"><div class="k">오늘 확인함</div><div class="v">${nChk} / ${all.length}</div></div>
    </div>
    ${filterBar(list)}
    <p class="note" id="count">${rows.length}개 표시 · 행을 누르면 차트와 지표, 위험 내용을 봅니다 (← → 로 다음 종목)</p>
    ${S.view[list] === "grid" ? gridHtml(list, rows) : tableHtml(list, rows)}`;
}

function bindList(list) {
  const f = S.filters[list];
  const save = () => { store.set("f." + list, f); renderList(list); };
  const main = $("#main");
  main.oninput = (e) => {
    const k = e.target.dataset.f;
    if (!k) return;
    if (k === "q") { f.q = e.target.value; const pos = e.target.selectionStart; save(); const q = $('[data-f="q"]'); q.focus(); q.setSelectionRange(pos, pos); }
    else if (k === "minTiming") { f.minTiming = +e.target.value; save(); }
  };
  main.onchange = (e) => {
    const k = e.target.dataset.f;
    if (!k || k === "q" || k === "minTiming") return;
    f[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    save();
  };
  main.onclick = (e) => {
    const t = e.target.closest("[data-sort],[data-excl],[data-excl-clear],[data-v],[data-quick],tr[data-i],.gcard");
    if (!t) return;
    if (t.dataset.sort) {
      const s = S.sort[list];
      if (s.k === t.dataset.sort) s.d = -s.d; else { s.k = t.dataset.sort; s.d = ["pos", "earn", "t", "risk"].includes(s.k) ? 1 : -1; }
      store.set("sort", S.sort); renderList(list);
    } else if (t.dataset.excl) {
      const c = t.dataset.excl;
      f.excl = f.excl.includes(c) ? f.excl.filter((x) => x !== c) : [...f.excl, c]; save();
    } else if (t.hasAttribute("data-excl-clear")) { f.excl = []; save(); }
    else if (t.dataset.v) { S.view[list] = t.dataset.v; store.set("view", S.view); renderList(list); }
    else if (t.dataset.quick === "buy") { Object.assign(f, DEFAULT_FILTERS, { stage: "매수 검토" }); save(); }
    else if (t.dataset.quick === "high") { Object.assign(f, DEFAULT_FILTERS, { risk: "only" }); save(); }
    else if (t.dataset.i !== undefined) openDetail(list, S.rows, +t.dataset.i);
  };
}

// ---------------------------------------------------------------- 상세
async function loadChart(t) {
  if (S.charts.has(t)) return S.charts.get(t);
  const res = await fetch(`data/charts/${encodeURIComponent(t)}.json`);
  if (!res.ok) throw new Error("차트 데이터 없음");
  const j = await res.json();
  S.charts.set(t, j);
  return j;
}

function openDetail(list, rows, i) {
  S.cur = { list, rows, i };
  $("#drawer").hidden = false;
  document.body.style.overflow = "hidden";
  renderDetail();
}
function closeDetail() {
  $("#drawer").hidden = true;
  document.body.style.overflow = "";
  if (S.chart) { S.chart.remove(); S.chart = null; }
  history.replaceState(null, "", "#" + S.tab);
  if (LISTS[S.tab]) renderList(S.tab);
}
function navDetail(step) {
  if (!S.cur) return;
  const n = S.cur.rows.length;
  S.cur.i = (S.cur.i + step + n) % n;
  renderDetail();
  $(".drawer-panel").scrollTop = 0;
}

function flagHtml(f, cats) {
  const link = f.url ? ` <a href="${safeUrl(f.url)}" target="_blank" rel="noopener">원문</a>` : "";
  return `<li class="flag"><span class="risk ${f.level}"><span class="ic">${RISK_IC[f.level]}</span></span>
    <div><div class="t">${esc(f.label)}<span class="cat">${esc(cats[f.cat] || f.cat)} · ${RISK_TXT[f.level]}</span></div>
    <div class="d">${esc(f.detail)}${link}</div></div></li>`;
}

function renderDetail() {
  const { list, rows, i } = S.cur;
  const r = rows[i];
  const D = S.data;
  history.replaceState(null, "", `#${list}/${r.t}`);
  $("#d-pos").textContent = `${LISTS[list]} ${i + 1} / ${rows.length}`;
  const W = D.weights[list === "box" ? "box" : "accum"];
  const parts = Object.entries(W).map(([k, w]) => {
    const v = (r.parts[k] || 0) * w;
    return `<div class="row"><span>${esc(D.weights.labels[k] || k)}</span><span class="track"><span class="fill" style="width:${(v / w) * 100}%"></span></span><span class="v">${v.toFixed(0)}/${w}</span></div>`;
  }).join("");
  const plan = `<div class="plan">
      <div><div class="k">현재가</div><div class="v">${px(r.price)}</div></div>
      <div><div class="k">손절가</div><div class="v down">${px(r.stop)}</div><div class="note">${pct(r.stop / r.price - 1, 0)}</div></div>
      <div><div class="k">${list === "box" ? "박스 상단" : "1년 고점"}</div><div class="v up">${px(r.target)}</div><div class="note">${pct(r.target / r.price - 1, 0)}</div></div>
    </div><p class="note" style="margin:6px 0 0">손익비 ${num(r.rr, 1)} = 목표까지 상승 폭 ÷ 손절까지 하락 폭. ${list === "box" ? "손절가 = 박스 하단 × 0.92 (박스가 깨진 것으로 보는 자리)." : "손절가 = 최근 20일 최저가 × 0.97."}</p>`;
  const sigs = r.signals.length ? r.signals.map((s) => `<li><span class="tone ${s.tone === "bull" ? "up" : s.tone === "bear" ? "down" : ""}">${s.tone === "bull" ? "▲" : s.tone === "bear" ? "▼" : "●"}</span><b>${esc(s.label)}</b> <span class="note">${esc(s.detail)}</span></li>`).join("") : `<li class="note">눈에 띄는 신호 없음</li>`;
  const flags = r.risk.flags.length ? r.risk.flags.map((f) => flagHtml(f, D.risk_categories)).join("") : `<li class="note">찾은 주의 신호가 없습니다. 그래도 매수 전 최근 공시·뉴스를 직접 확인하세요.</li>`;
  const news = r.news.length ? r.news.map((n) => `<li class="${n.flag === "high" || n.flag === "mid" ? "news-hit" : ""}"><span class="note">${esc(n.date.slice(5, 10))}</span> <a href="${safeUrl(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>${n.flag ? ` <span class="risk ${n.flag}"><span class="ic">${RISK_IC[n.flag]}</span></span>` : ""}</li>`).join("") : `<li class="note">최근 뉴스 없음</li>`;
  const analyst = (r.analyst || []).map((a) => `<li><span class="note">${esc(a.date.slice(5))}</span> ${esc(a.firm)} · ${esc(a.from ? a.from + " → " : "")}${esc(a.to)}${fin(a.pt) ? ` · 목표가 ${fin(a.pt_prior) ? px(a.pt_prior) + "→" : ""}${px(a.pt)}` : ""}</li>`).join("");
  const filings = (r.filings || []).map((f) => `<li><span class="note">${esc(f.date.slice(5))}</span> <a href="${safeUrl(f.url)}" target="_blank" rel="noopener">${esc(f.form)}</a> ${esc(f.items || "")}</li>`).join("");
  const info = list === "box"
    ? `<dt>박스</dt><dd>${px(r.box.low)} ~ ${px(r.box.high)}</dd><dt>기간 · 폭 · 왕복</dt><dd>${r.box.years}년 · ${num(r.box.band, 1)}배 · ${Math.floor(r.box.legs / 2)}회</dd>
       <dt>박스 안 위치</dt><dd>${num(r.box.pos * 100)}%</dd><dt>박스 점수</dt><dd>${num(r.score)}</dd><dt>KRUS 실적 지문 차이</dt><dd>${num(r.krus, 2)}</dd>`
    : `<dt>매집 점수</dt><dd>${num(r.score, 2)}</dd><dt>거래량 쏠림 · CMF</dt><dd>${num(r.acc.stealth, 2)} · ${num(r.acc.cmf, 2)}</dd>
       <dt>흡수형 대량거래</dt><dd>${num(r.acc.absorption)}일</dd><dt>변동성 수축</dt><dd>${num(r.acc.contraction, 2)}</dd><dt>52주 위치</dt><dd>${fin(r.acc.pos52) ? num(r.acc.pos52 * 100) + "%" : "–"}</dd>`;
  const chk = isChecked(r.t);
  const last = S.chk[r.t] && !chk ? `<span class="note">지난 확인 ${esc(S.chk[r.t])}</span>` : "";
  $("#d-body").innerHTML = `
    <div class="dh">
      <div><h2 id="d-title">${esc(r.t)} ${tkBadges(r, list)}</h2><div class="note">${esc(r.name)} · ${esc(r.sector || "")} / ${esc(r.industry || "")} · ${money(r.mcap)}</div></div>
      <div class="px">${px(r.price)} ${pctC(r.chg1d)}</div>
      <div>${stageBadge(r.stage)} <span class="note">타이밍</span> <b>${num(r.timing)}</b></div>
      <div>${riskBadge(r.risk)}</div>
      <span class="grow"></span>
      <div class="links"><a href="https://finance.yahoo.com/quote/${encodeURIComponent(r.t)}" target="_blank" rel="noopener">야후</a><a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(r.t)}" target="_blank" rel="noopener">트레이딩뷰</a><a href="https://finviz.com/quote.ashx?t=${encodeURIComponent(r.t)}" target="_blank" rel="noopener">Finviz</a><a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(r.t)}&type=&dateb=&owner=include&count=40" target="_blank" rel="noopener">SEC 공시</a></div>
    </div>
    <div class="check-row"><button class="btn ${chk ? "primary" : ""}" id="chk-btn">${chk ? "✓ 오늘 확인함" : "확인 완료로 표시 (C)"}</button> ${last}</div>
    <div class="cols">
      <div>
        <div class="chart-wrap">
          <div class="chart-tools">
            <span class="seg" id="periods">${PERIODS.map(([k]) => `<button data-p="${k}" class="${S.period === k ? "on" : ""}">${k}</button>`).join("")}</span>
            <div class="chips" id="ovs">${OVERLAYS.map(([k, t, c]) => `<button class="chip${S.ov[k] ? " on" : ""}" data-ov="${k}"><span class="sw" style="background:var(${c})"></span>${t}</button>`).join("")}</div>
            <div class="chips" id="pns"><span class="note">아래 창:</span>${PANES.map(([k, t]) => `<button class="chip${S.panes[k] ? " on" : ""}" data-pn="${k}">${t}</button>`).join("")}</div>
          </div>
          <div class="chart-legend" id="legend"></div>
          <div class="chart" id="chart"><p class="note" style="padding:20px">차트 불러오는 중…</p></div>
        </div>
        <h3>지표 신호 (마지막 거래일 기준)</h3>
        <div class="card"><ul class="list">${sigs}</ul>
          <p class="note" style="margin:8px 0 0">RSI ${num(r.ind.rsi)} · 스토캐스틱 RSI ${num(r.ind.stoch_k)} · MFI ${num(r.ind.mfi)} · ADX ${num(r.ind.adx)} · 볼린저 %B ${num(r.ind.bb_pctb, 2)} ·
          50일선 대비 ${pct(r.ind.vs_sma50)} · 200일선 대비 ${pct(r.ind.vs_sma200)} · 하루 변동폭(ATR) ${pct(r.ind.atr_pct, 1, false)} · 슈퍼트렌드 ${r.ind.st_dir === 1 ? "상승" : r.ind.st_dir === -1 ? "하락" : "–"}</p></div>
        <h3>최근 뉴스</h3>
        <div class="card"><ul class="list">${news}</ul></div>
        ${analyst || filings ? `<h3>애널리스트 · 공시</h3><div class="card"><ul class="list">${analyst}${filings}</ul></div>` : ""}
      </div>
      <div>
        <h3 style="margin-top:4px">매수 주의</h3>
        <div class="card"><ul class="list">${flags}</ul>${D.sec_enabled ? "" : `<p class="note" style="margin:8px 0 0">SEC 공시(증자 신고서 등) 확인은 꺼져 있습니다. 켜는 방법은 '설명' 탭에 있습니다.</p>`}</div>
        <h3>타이밍 점수 ${num(r.timing)} / 100</h3>
        <div class="card parts">${parts}${plan}</div>
        <h3>${list === "box" ? "박스" : "매집 흔적"}</h3>
        <div class="card"><dl class="kv">${info}
          <dt>다음 실적</dt><dd>${r.next_earn ? esc(r.next_earn) + ` (D-${r.earn_days})` : "–"}</dd>
          <dt>공매도 / 유통주식</dt><dd>${pct(r.short_float, 1, false)}</dd><dt>기관 보유</dt><dd>${pct(r.inst_pct, 0, false)}</dd>
          <dt>내부자 매수 (90일)</dt><dd>${r.insider_buy_90d ? money(r.insider_buy_90d) : "–"}</dd>
          <dt>애널리스트 평균</dt><dd>${esc(r.recommendation && r.recommendation !== "none" ? r.recommendation : "–")}${fin(r.target_mean) ? ` · 목표 ${px(r.target_mean)}` : ""}</dd>
          <dt>1개월 · 3개월 · 1년</dt><dd>${pctC(r.ret1m, 0)} · ${pctC(r.ret3m, 0)} · ${pctC(r.ret1y, 0)}</dd>
          <dt>하루 거래대금</dt><dd>${money(r.dv)}</dd>
          ${r.first_seen ? `<dt>처음 목록에 오른 날</dt><dd>${esc(r.first_seen)}</dd>` : ""}
        </dl>${r.summary ? `<p class="summary">${esc(r.summary)}…</p>` : ""}</div>
        <h3>내 메모</h3>
        <div class="card"><textarea class="memo" id="memo" placeholder="판단 메모 (예: 실적 후 재확인, 40달러 이하 분할 매수)">${esc(S.memo[r.t] || "")}</textarea>
          <p class="note" style="margin:4px 0 0">확인 표시와 메모는 이 브라우저에만 저장됩니다.</p></div>
      </div>
    </div>`;
  $("#chk-btn").onclick = toggleCheck;
  $("#memo").oninput = (e) => { S.memo[r.t] = e.target.value; store.set("memo", S.memo); };
  $("#periods").onclick = (e) => { const b = e.target.closest("[data-p]"); if (!b) return; S.period = b.dataset.p; store.set("period", S.period); renderDetail(); };
  $("#ovs").onclick = (e) => { const b = e.target.closest("[data-ov]"); if (!b) return; S.ov[b.dataset.ov] = !S.ov[b.dataset.ov]; store.set("ov", S.ov); drawChart(r, list); b.classList.toggle("on"); };
  $("#pns").onclick = (e) => { const b = e.target.closest("[data-pn]"); if (!b) return; S.panes[b.dataset.pn] = !S.panes[b.dataset.pn]; store.set("panes", S.panes); drawChart(r, list); b.classList.toggle("on"); };
  drawChart(r, list);
}

function toggleCheck() {
  const r = S.cur.rows[S.cur.i];
  if (isChecked(r.t)) delete S.chk[r.t]; else S.chk[r.t] = S.data.asof;
  store.set("chk", S.chk);
  renderDetail();
}

// ---------------------------------------------------------------- 차트
function timeKey(t) {
  if (typeof t === "string") return t;
  if (t && typeof t === "object") return `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;
  return null;
}

async function drawChart(r, list) {
  const el = $("#chart");
  let cd;
  try { cd = await loadChart(r.t); } catch (e) { el.innerHTML = `<p class="note" style="padding:20px">${esc(e.message)}</p>`; return; }
  if (!S.cur || S.cur.rows[S.cur.i].t !== r.t) return; // 그 사이 다른 종목으로 넘어감
  if (S.chart) { S.chart.remove(); S.chart = null; }
  el.innerHTML = "";
  const C = (n) => cssVar(n);
  const up = C("--up"), down = C("--down");
  const chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: C("--surface") }, textColor: C("--ink2"), fontSize: 11, attributionLogo: true,
      fontFamily: getComputedStyle(document.body).fontFamily, panes: { separatorColor: C("--line"), enableResize: true } },
    grid: { vertLines: { color: C("--grid") }, horzLines: { color: C("--grid") } },
    rightPriceScale: { borderColor: C("--line") },
    timeScale: { borderColor: C("--line"), rightOffset: 4 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: "ko-KR" },
  });
  S.chart = chart;
  const dates = cd.dates;
  const nd = cd.nd;
  const fmt = { type: "price", precision: nd, minMove: 1 / 10 ** nd };
  const candle = chart.addSeries(LW.CandlestickSeries, {
    upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down, priceFormat: fmt,
  }, 0);
  candle.setData(dates.map((d, i) => ({ time: d, open: cd.o[i], high: cd.h[i], low: cd.l[i], close: cd.c[i] })).filter((b) => fin(b.close) && fin(b.open)));
  const lineData = (arr) => dates.map((d, i) => ({ time: d, value: arr[i] })).filter((p) => fin(p.value));
  const addLine = (arr, color, pane = 0, extra = {}) => {
    const s = chart.addSeries(LW.LineSeries, { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: fmt, ...extra }, pane);
    s.setData(lineData(arr));
    return s;
  };
  const legendSeries = [];
  const I = cd.ind;
  if (S.ov.sma20) legendSeries.push(["20일", addLine(I.sma20, C("--s-orange")), I.sma20]);
  if (S.ov.sma50) legendSeries.push(["50일", addLine(I.sma50, C("--s-violet")), I.sma50]);
  if (S.ov.sma200) legendSeries.push(["200일", addLine(I.sma200, C("--s-green")), I.sma200]);
  if (S.ov.bb) {
    const m = C("--muted");
    addLine(I.bb_up, m, 0, { lineWidth: 1, lineStyle: LW.LineStyle.Dashed });
    addLine(I.bb_mid, m, 0, { lineWidth: 1, lineStyle: LW.LineStyle.Dotted });
    addLine(I.bb_low, m, 0, { lineWidth: 1, lineStyle: LW.LineStyle.Dashed });
  }
  if (S.ov.st) {
    const s = chart.addSeries(LW.LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: fmt }, 0);
    s.setData(dates.map((d, i) => ({ time: d, value: I.st_line[i], color: I.st_dir[i] === 1 ? up : down })).filter((p) => fin(p.value)));
  }
  if (S.ov.avwap) legendSeries.push(["VWAP", addLine(I.avwap, C("--s-magenta")), I.avwap]);
  if (S.ov.box) {
    const y = C("--s-yellow");
    if (cd.box) {
      candle.createPriceLine({ price: cd.box.high, color: y, lineWidth: 2, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: "박스 상단" });
      candle.createPriceLine({ price: cd.box.low, color: y, lineWidth: 2, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: "박스 하단" });
    } else if (fin(r.target)) {
      candle.createPriceLine({ price: r.target, color: y, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: "1년 고점" });
    }
    if (fin(r.stop)) candle.createPriceLine({ price: r.stop, color: C("--critical"), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: "손절" });
  }
  const idx = new Map(dates.map((d, i) => [d, i]));
  const markers = [];
  const snap = (d) => { for (const x of dates) if (x >= d) return x; return null; };
  if (S.ov.earn) for (const e of cd.earnings || []) { const t = snap(e); if (t && e <= dates[dates.length - 1]) markers.push({ time: t, position: "aboveBar", color: C("--ink2"), shape: "circle", text: "실적" }); }
  if (cd.first_seen) { const t = snap(cd.first_seen); if (t) markers.push({ time: t, position: "belowBar", color: C("--accent"), shape: "arrowUp", text: "목록 편입" }); }
  markers.sort((a, b) => (a.time < b.time ? -1 : 1));
  if (markers.length) LW.createSeriesMarkers(candle, markers);

  let pane = 0;
  const lower = [];
  const guide = (s, v) => s.createPriceLine({ price: v, color: C("--muted"), lineWidth: 1, lineStyle: LW.LineStyle.Dotted, axisLabelVisible: false });
  const plain = { type: "price", precision: 1, minMove: 0.1 };
  if (S.panes.vol) {
    pane += 1;
    const s = chart.addSeries(LW.HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, pane);
    s.setData(dates.map((d, i) => ({ time: d, value: cd.v[i], color: (cd.c[i] >= cd.o[i] ? up : down) + "99" })).filter((p) => fin(p.value)));
    lower.push(["거래량", (i) => fmtVol(cd.v[i])]);
  }
  if (S.panes.rsi) {
    pane += 1;
    const s = addLine(I.rsi, C("--s-violet"), pane, { priceFormat: plain, lastValueVisible: true });
    guide(s, 30); guide(s, 70);
    lower.push(["RSI", (i) => num(I.rsi[i], 1)]);
  }
  if (S.panes.macd) {
    pane += 1;
    const h = chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 3, minMove: 0.001 } }, pane);
    h.setData(dates.map((d, i) => ({ time: d, value: I.macd_hist[i], color: (I.macd_hist[i] >= 0 ? up : down) + "88" })).filter((p) => fin(p.value)));
    addLine(I.macd, C("--down"), pane, { lineWidth: 1, priceFormat: { type: "price", precision: 3, minMove: 0.001 } });
    addLine(I.macd_signal, C("--s-orange"), pane, { lineWidth: 1, priceFormat: { type: "price", precision: 3, minMove: 0.001 } });
    lower.push(["MACD", (i) => `${num(I.macd[i], 3)} / 시그널 ${num(I.macd_signal[i], 3)}`]);
  }
  if (S.panes.stoch) {
    pane += 1;
    const k = addLine(I.stoch_k, C("--down"), pane, { lineWidth: 1, priceFormat: plain });
    addLine(I.stoch_d, C("--s-orange"), pane, { lineWidth: 1, priceFormat: plain });
    guide(k, 20); guide(k, 80);
    lower.push(["StochRSI", (i) => `K ${num(I.stoch_k[i])} D ${num(I.stoch_d[i])}`]);
  }
  if (S.panes.adx) {
    pane += 1;
    const a = addLine(I.adx, C("--ink2"), pane, { priceFormat: plain });
    addLine(I.plus_di, up, pane, { lineWidth: 1, priceFormat: plain });
    addLine(I.minus_di, down, pane, { lineWidth: 1, priceFormat: plain });
    guide(a, 25);
    lower.push(["ADX", (i) => `${num(I.adx[i])} (+DI ${num(I.plus_di[i])} / −DI ${num(I.minus_di[i])})`]);
  }
  if (S.panes.mfi) {
    pane += 1;
    const m = addLine(I.mfi, C("--s-magenta"), pane, { priceFormat: plain });
    guide(m, 20); guide(m, 80);
    lower.push(["MFI", (i) => num(I.mfi[i])]);
  }
  if (S.panes.obv) {
    pane += 1;
    addLine(I.obv, C("--s-green"), pane, { priceFormat: { type: "volume" } });
    lower.push(["OBV", (i) => fmtVol(I.obv[i])]);
  }
  const panes = chart.panes();
  panes.forEach((p, k) => p.setStretchFactor(k === 0 ? Math.max(2.4, pane * 0.9) : 1));

  const n = (PERIODS.find(([k]) => k === S.period) || PERIODS[2])[1];
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, dates.length - n), to: dates.length + 3 });

  const legend = $("#legend");
  const showAt = (i) => {
    if (i == null) return;
    const ch = i > 0 ? cd.c[i] / cd.c[i - 1] - 1 : null;
    legend.innerHTML = `<span><b>${dates[i]}</b></span><span>시 ${px(cd.o[i])}</span><span>고 ${px(cd.h[i])}</span><span>저 ${px(cd.l[i])}</span><span>종 ${px(cd.c[i])} ${pctC(ch)}</span>`
      + legendSeries.map(([nm, , arr]) => `<span>${nm} ${px(arr[i])}</span>`).join("")
      + lower.map(([nm, f]) => `<span>${nm} ${f(i)}</span>`).join("");
  };
  showAt(dates.length - 1);
  chart.subscribeCrosshairMove((p) => {
    const k = timeKey(p && p.time);
    showAt(k != null && idx.has(k) ? idx.get(k) : dates.length - 1);
  });
}
function fmtVol(v) {
  if (!fin(v)) return "–";
  const a = Math.abs(v);
  return a >= 1e9 ? (v / 1e9).toFixed(1) + "B" : a >= 1e6 ? (v / 1e6).toFixed(1) + "M" : a >= 1e3 ? (v / 1e3).toFixed(0) + "K" : v.toFixed(0);
}

// ---------------------------------------------------------------- 성과 추적 · 업황 · 설명
function renderTrack() {
  const T = S.data.tracking;
  const block = (k) => {
    const t = T[k];
    if (!t || !t.rows.length) return `<h2>${LISTS[k]}</h2><div class="card note">아직 추적할 기록이 없습니다. 매일 쌓이면서 채워집니다.</div>`;
    const sm = t.summary.map((s) => `<tr><td class="l">${esc(s.bucket)}</td><td class="n">${s.n}</td><td class="n">${pctC(s.ret)}</td><td class="n">${pctC(s.excess)}</td><td class="n">${num(s.win * 100)}%</td>${k === "box" ? `<td class="n">${num(s.top * 100)}%</td><td class="n">${num(s.broke * 100)}%</td>` : ""}</tr>`).join("");
    const rows = t.rows.slice(0, 80).map((x) => `<tr><td class="l tk"><b>${esc(x.ticker)}</b> <span class="badge">${esc(x.status)}</span></td><td class="l wrap">${esc(x.name || "")}</td>
      <td class="n">${esc(String(x.first_date).slice(0, 10))} (${x.first_rank}위)</td><td class="n">${px(x.first_price)}</td><td class="n">${px(x.price)}</td>
      <td class="n">${pctC(x.ret)}</td><td class="n">${pctC(x.excess)}</td><td class="n">${pctC(x.max_up, 0)} / ${pctC(x.max_down, 0)}</td><td class="n">${x.days}일</td></tr>`).join("");
    return `<h2>${LISTS[k]}</h2>
      <div class="tbl"><table><thead><tr><th class="l">처음 오른 뒤</th><th>종목 수</th><th>평균 수익률</th><th>S&amp;P 500 대비</th><th>시장을 이긴 비율</th>${k === "box" ? "<th>상단 도달</th><th>박스 이탈</th>" : ""}</tr></thead><tbody>${sm}</tbody></table></div>
      <div class="tbl" style="margin-top:8px"><table><thead><tr><th class="l">종목</th><th class="l">이름</th><th>처음 오른 날</th><th>그날 종가</th><th>현재가</th><th>수익률</th><th>S&amp;P 500 대비</th><th>최고 / 최저</th><th>경과</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  };
  $("#main").innerHTML = `<p class="note">목록에 처음 오른 날 종가에 샀다면 지금까지 얼마인지 기록합니다. 몇 달 쌓이면 이 방법이 실제로 통하는지 숫자로 확인할 수 있습니다.
    <a href="data/history.csv">기록 CSV 받기</a></p>${block("box")}${block("accum")}`;
  $("#main").onclick = $("#main").oninput = $("#main").onchange = null;
}

function renderSector() {
  const rows = S.data.sectors.map((s) => `<tr><td class="l"><b>${esc(s.name)}</b> <span class="note">${esc(s.etf)}</span></td>
    <td class="n">${pctC(s.ret1m)}</td><td class="n">${pctC(s.ret3m)}</td><td class="n">${pctC(s.vs_spy3m)}</td>
    <td class="l">${s.above200 ? '<span class="up">▲ 위</span>' : '<span class="down">▼ 아래</span>'}</td></tr>`).join("");
  $("#main").innerHTML = `<p class="note">업종 ETF 흐름입니다. 3개월 동안 S&amp;P 500보다 5%p 넘게 약하거나 200일선 아래면 그 업종 종목에 '업종 약세' 주의가 붙습니다.</p>
    <div class="tbl"><table><thead><tr><th class="l">업종</th><th>1개월</th><th>3개월</th><th>S&amp;P 500 대비 (3개월)</th><th class="l">200일선</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  $("#main").onclick = $("#main").oninput = $("#main").onchange = null;
}

function renderHelp() {
  const W = S.data.weights;
  const w = (o) => Object.entries(o).map(([k, v]) => `${esc(W.labels[k] || k)} ${v}`).join(" · ");
  $("#main").innerHTML = `<div class="card help">
    <h2 style="margin-top:4px">이 페이지는</h2>
    <p>평일 미국 장이 끝나면 자동으로 미국 상장 종목 전체를 훑어서 <b>박스 하단권</b>과 <b>매집 흔적</b> 두 목록을 만듭니다. 매일 아침 목록을 타이밍 순으로 보고, 하나씩 차트를 열어 판단한 뒤 <b>확인 완료</b>로 표시하면 됩니다. 확인 표시와 메모는 이 브라우저에만 저장됩니다.</p>
    <h3>단계</h3>
    <ul><li><b>매수 검토</b>: 타이밍 점수 60 이상이고 위험이 '높음'이 아닌 종목</li><li><b>관찰</b>: 타이밍 40 이상</li><li><b>대기</b>: 그 밖</li></ul>
    <h3>타이밍 점수 (100점)</h3>
    <ul><li><b>박스 하단권</b>: ${w(W.box)}. 손익비 = (박스 상단 − 현재가) ÷ (현재가 − 손절가), 손절가 = 박스 하단 × 0.92.</li>
      <li><b>매집 흔적</b>: ${w(W.accum)}.</li></ul>
    <h3>매수 주의 (위험 신호)</h3>
    <ul><li><b>증자·희석</b>: 최근 뉴스 제목의 유상증자·전환사채·주식 병합, SEC 증권신고서(S-1/S-3/F-1/F-3/424B). 소형주 급락의 가장 흔한 원인입니다.</li>
      <li><b>상장 유지</b>: 주가 $1 미만, 나스닥 상장 기준 미달 통지, 보고서 제출 지연, 파산.</li>
      <li><b>악재 뉴스·소송·조사</b>: 실적 전망 하향, 경영진 사임, 임상 실패, 공매도 리포트, 당국 조사.</li>
      <li><b>애널리스트</b>: 최근 30일 투자의견 하향, 목표가 하향, 평균 목표가가 현재가보다 낮음.</li>
      <li><b>재무</b>: 현금이 1~2년치 현금 소진액보다 적음(증자 가능성), 큰 적자, 부채 과다.</li>
      <li><b>업황</b>: 그 업종 ETF가 200일선 아래이거나 3개월 동안 S&amp;P 500보다 5%p 넘게 약함.</li>
      <li><b>주가 흐름·급등락</b>: 3개월 −30% 급락, 박스 이탈, 최근 급등(작전·덤핑 위험), 큰 변동성. <b>실적 임박</b>: 7일 이내.</li></ul>
    <p>뉴스는 제목의 단어로 판단하므로 놓치거나 잘못 잡는 경우가 있습니다. '원문' 링크로 꼭 직접 확인하세요.</p>
    <p>SEC 공시 확인: 지금 <b>${S.data.sec_enabled ? "켜짐" : "꺼짐"}</b>. SEC 는 연락처 이메일을 밝힌 요청만 받으므로, 켜려면 GitHub 저장소
      Settings → Secrets and variables → Actions → <b>Variables</b> 에 <code>SEC_CONTACT_EMAIL</code> (본인 이메일)을 추가하세요. 다음 실행부터 적용됩니다.</p>
    <h3>지표</h3>
    <ul><li><b>RSI</b> 30 아래 과매도, 70 위 과매수. <b>스토캐스틱 RSI</b>는 RSI를 더 민감하게 만든 것 (20 아래에서 상향 교차 = 반등 신호).</li>
      <li><b>MACD</b> 시그널선 상향 돌파 = 골든크로스. 히스토그램이 음수에서 올라오면 하락 힘이 약해지는 중.</li>
      <li><b>볼린저 밴드</b> 하단 밖으로 나갔다 들어오면 반등 신호. 폭이 6개월 중 가장 좁으면(스퀴즈) 큰 움직임 전조.</li>
      <li><b>슈퍼트렌드</b> ATR 기반 추세선. 빨강 = 상승 추세, 파랑 = 하락 추세.</li>
      <li><b>ADX</b> 25 위면 추세가 강함, 20 아래면 횡보 (박스권에 유리). <b>MFI</b> 거래량을 반영한 RSI.</li>
      <li><b>OBV</b> 오른 날 거래량은 더하고 내린 날은 빼서 쌓은 값. 주가는 그대로인데 OBV가 오르면 매집 신호.</li>
      <li><b>앵커드 VWAP</b> 52주 최저점 이후 거래된 평균 가격. 그 위로 올라서면 그 뒤에 산 사람들이 평균적으로 이익 구간.</li></ul>
    <h3>먼저 알아둘 것</h3>
    <ul><li><b>이 목록은 조건 검색 결과이며 매수 추천이 아닙니다.</b></li>
      <li>과거 검증에서 박스 하단 매수는 3개월 후 시장 대비 +1.9%였지만 통계적으로 의미가 없었고, 박스 아래로 25% 넘게 이탈한 종목은 이후 3개월 시장 대비 −10.7%였습니다.</li>
      <li>매집 흔적(거래량 쏠림·CMF 등)은 2022.11~2026.6 검증에서 이후 수익률을 예측하지 못했습니다. 소형주는 증자·급등락 위험이 특히 큽니다.</li>
      <li><b>타이밍 점수 과거 검증</b> (2023.10~2026.6, 2개월마다 17개 시점, 그날까지의 데이터로 다시 계산):
        박스 하단권은 타이밍 상위 절반이 하위 절반보다 1개월 후 +3.7%p, 3개월 후 +5.2%p 높았고 타이밍 60 이상은 S&amp;P 500 대비 3개월 +11.5% (23개)였지만,
        표본이 적어 통계적으로 확실하지 않습니다 (t≈0.8). 매집 흔적의 타이밍 점수는 이후 수익률을 예측하지 못했습니다 (60 이상 3개월 −2.0%).
        현재 상장 종목만으로 계산해서 상장폐지 종목이 빠진 만큼 결과가 좋게 나옵니다. '성과 추적' 탭에 실제 결과가 쌓입니다.</li></ul>
    <p class="note">데이터: 야후 파이낸스(주가·뉴스·애널리스트·재무), SEC EDGAR(공시). 차트: TradingView Lightweight Charts.</p>
  </div>`;
  $("#main").onclick = $("#main").oninput = $("#main").onchange = null;
}

// ---------------------------------------------------------------- 탭·테마·시작
function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  if (LISTS[tab]) { renderList(tab); bindList(tab); }
  else if (tab === "track") renderTrack();
  else if (tab === "sector") renderSector();
  else renderHelp();
  window.scrollTo(0, 0);
}

function applyTheme() {
  const t = store.get("theme", "auto");
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  $("#theme-btn").textContent = t === "auto" ? "◐" : t === "dark" ? "☾" : "☀";
  $("#theme-btn").title = `화면 테마: ${t === "auto" ? "기기 설정" : t === "dark" ? "어두운" : "밝은"}`;
}

async function init() {
  applyTheme();
  $("#theme-btn").onclick = () => {
    const order = ["auto", "light", "dark"];
    store.set("theme", order[(order.indexOf(store.get("theme", "auto")) + 1) % 3]);
    applyTheme();
    if (S.cur && !$("#drawer").hidden) drawChart(S.cur.rows[S.cur.i], S.cur.list);
  };
  try {
    const res = await fetch("data/latest.json", { cache: "no-cache" });
    S.data = await res.json();
  } catch (e) {
    $("#main").innerHTML = `<div class="card">데이터를 불러오지 못했습니다: ${esc(e.message)}</div>`;
    return;
  }
  const D = S.data;
  $("#meta").textContent = `기준일 ${D.asof} (미국 장 마감) · 갱신 ${D.updated} KST · 미국 ${D.universe.all.toLocaleString()}개 종목 중 박스 하단권 ${D.lists.box.length} · 매집 흔적 ${D.lists.accum.length}`;
  $("#main").addEventListener("toggle", (e) => { if (e.target.matches("details.fwrap")) S.fopen = e.target.open; }, true);
  document.querySelector(".tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { history.replaceState(null, "", "#" + b.dataset.tab); setTab(b.dataset.tab); } };
  $("#drawer").onclick = (e) => {
    if (e.target.closest("[data-close]")) closeDetail();
    const nb = e.target.closest("[data-nav]");
    if (nb) navDetail(+nb.dataset.nav);
  };
  document.addEventListener("keydown", (e) => {
    if ($("#drawer").hidden || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "Escape") closeDetail();
    else if (e.key === "ArrowRight") navDetail(1);
    else if (e.key === "ArrowLeft") navDetail(-1);
    else if (e.key === "c" || e.key === "C") toggleCheck();
  });
  const [tab, tk] = decodeURIComponent(location.hash.slice(1)).split("/");
  setTab(LISTS[tab] || ["track", "sector", "help"].includes(tab) ? tab : "box");
  if (tk && LISTS[tab]) {
    const rows = filtered(tab);
    let i = rows.findIndex((r) => r.t === tk);
    if (i < 0) { const all = D.lists[tab]; i = all.findIndex((r) => r.t === tk); if (i >= 0) openDetail(tab, all, i); }
    else openDetail(tab, rows, i);
  }
}
init();

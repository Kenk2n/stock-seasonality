"use strict";
/* 주식 후보 모니터: data/latest.json + data/charts/<종목>.json 을 읽어 표·차트로 보여준다. */

const LW = window.LightweightCharts;
const LISTS = { box: "박스 하단권", accum: "매집 흔적" };
const RISK_TXT = { high: "높음", mid: "주의", low: "낮음", none: "없음" };
const RISK_IC = { high: "!", mid: "!", low: "·", none: "✓" };
const RISK_ORD = { none: 0, low: 1, mid: 2, high: 3 };
const STAGE_CLS = { "매수 검토": "buy", "관찰": "watch", "대기": "wait" };
const STAGE_RANK = { "매수 검토": 2, "관찰": 1, "대기": 0 };
const DEFAULT_FILTERS = { bp: "all", q: "", stage: "all", risk: "all", excl: [], noEarn: false, mcap: "all", minTiming: 0, hideChecked: false };
const OVERLAYS = [
  ["box", "박스 구간·손절", "--s-yellow"], ["swing", "고점·저점", "--up"], ["sma20", "20일선", "--s-orange"], ["sma50", "50일선", "--s-violet"],
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
  sort: store.get("sort", { box: { k: "stage", d: -1 }, accum: { k: "stage", d: -1 } }),
  filters: { box: { ...DEFAULT_FILTERS, ...store.get("f.box", {}) }, accum: { ...DEFAULT_FILTERS, ...store.get("f.accum", {}) } },
  ov: { box: true, swing: true, sma20: false, sma50: true, sma200: false, bb: false, st: false, avwap: false, earn: false, ...store.get("ov", {}) },
  panes: { vol: true, rsi: true, macd: !matchMedia("(max-width: 700px)").matches, stoch: false, adx: false, mfi: false, obv: false, ...store.get("panes", {}) },
  period: { box: store.get("period.box", "BOX"), accum: store.get("period.accum", "1Y") },
  chk: store.get("chk", {}), memo: store.get("memo", {}),
  charts: new Map(), cur: null, chart: null,
  sim: { data: null, L: store.get("simL", "all"), dL: store.get("simDL", "32"), q: "", mcap: "all", files: new Map() },
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
  if (r.is_ref) b += `<span class="badge both" title="쿠라 스시 (박스 기준 종목)">기준</span>`;
  if (r.new) b += `<span class="badge new">NEW</span>`;
  else if (r.streak > 1) b += `<span class="badge">${r.streak}일째</span>`;
  if (list === "box" && r.in_accum) b += `<span class="badge both" title="매집 흔적 목록에도 있음">매집</span>`;
  if (list === "accum" && r.in_box) b += `<span class="badge both" title="박스 하단권 목록에도 있음">박스</span>`;
  return b;
}

// 박스 하단권 · 비슷한 차트 분석 범위 설명
function scopeTxt() {
  const f = (S.data && S.data.filters) || {};
  return `시총 ${money(f.box_min_mcap)} · 하루 거래대금 ${money(f.box_min_dv)} · 주가 $${f.box_min_price ?? "–"} 이상 미국 종목`;
}

// ---------------------------------------------------------------- 필터·정렬
const BOX_GROUPS = [["5", "4~5년"], ["3", "2~3년"], ["1", "1년"]];
const groupLabel = (g) => (BOX_GROUPS.find(([k]) => k === g) || [, ""])[1];
// 박스 기간 묶음 g 기준으로 본 행 (박스 지표·주봉 그림·타이밍·손절·목표가 그 기간 박스 기준)
function boxRow(r, g) {
  return g && r.boxes && r.boxes[g] ? { ...r, ...r.boxes[g], group: g } : r;
}

function filtered(list) {
  const f = S.filters[list];
  const q = f.q.trim().toLowerCase();
  let base = S.data.lists[list];
  if (list === "box" && f.bp !== "all") base = base.filter((r) => r.boxes && r.boxes[f.bp]).map((r) => boxRow(r, f.bp));
  let rows = base.filter((r) => {
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
    timing: (r) => r.timing, stage: (r) => STAGE_RANK[r.stage] * 1000 + r.timing, score: (r) => r.score, rr: (r) => r.rr, rsi: (r) => r.ind.rsi, risk: (r) => RISK_ORD[r.risk.level] * 10 + r.risk.flags.length,
    mcap: (r) => r.mcap, chg1d: (r) => r.chg1d, ret3m: (r) => r.ret3m, pos: (r) => (r.box ? r.box.pos : r.acc.pos52),
    to_target: (r) => (r.box ? r.box.to_target : null), amp: (r) => (r.box ? r.box.amp : null),
    trips: (r) => (r.box ? r.box.round_trips : null), earn: (r) => r.earn_days ?? 999, bull: (r) => r.bull - r.bear,
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
    ["#", null], ["종목", "t", "l"], ["차트", null, "l"], ["단계", "stage", "l"], ["타이밍", "timing"], ["위험", "risk", "l"], ["현재가 · 1일", "chg1d"],
    ["진폭", "amp"], ["왕복", "trips"], ["저점 구간", null], ["고점 구간", null], ["박스 위치", "pos"], ["목표까지", "to_target"],
    ["손익비", "rr"], ["RSI", "rsi"], ["신호 ▲/▼", "bull"], ["실적", "earn"], ["이름 · 업종", null, "l"],
  ],
  accum: [
    ["#", null], ["종목", "t", "l"], ["차트", null, "l"], ["단계", "stage", "l"], ["타이밍", "timing"], ["위험", "risk", "l"], ["현재가 · 1일", "chg1d"],
    ["매집 점수", "score"], ["52주 위치", "pos"], ["3개월", "ret3m"], ["거래량 5일/50일", "vol"], ["RSI", "rsi"], ["신호 ▲/▼", "bull"],
    ["시총", "mcap"], ["실적", "earn"], ["이름 · 업종", null, "l"],
  ],
};

function rowCells(list, r, i) {
  const common = [
    `<td class="n">${i + 1}</td>`,
    `<td class="l tk"><b>${esc(r.t)}</b>${tkBadges(r, list)}</td>`,
    `<td class="thumb">${sparkSvg(r)}</td>`,
    `<td class="l">${stageBadge(r.stage)}</td>`,
    `<td>${meter(r.timing)}</td>`,
    `<td class="l">${riskBadge(r.risk)}</td>`,
    `<td class="n">${px(r.price)} ${pctC(r.chg1d)}</td>`,
  ];
  const earn = `<td class="n">${fin(r.earn_days) ? `D-${r.earn_days}` : "–"}</td>`;
  const name = `<td class="l wrap">${esc(r.name)}<br><span class="note">${esc(r.industry || "")}</span></td>`;
  const sig = `<td class="sig"><span class="up">▲${r.bull}</span> / <span class="down">▼${r.bear}</span></td>`;
  if (list === "box") {
    const b = r.box;
    return common.concat([
      `<td class="n">${num(b.amp, 1)}배</td>`, `<td class="n">${num(b.round_trips, 1)}회<span class="note"> /${b.years}년</span></td>`,
      `<td class="n down">${zone(b.low_zone)}</td>`, `<td class="n up">${zone(b.high_zone)}</td>`,
      `<td class="n">${num(b.pos * 100)}%</td>`, `<td class="n">${pctC(b.to_target, 0)}</td>`,
      `<td class="n">${num(r.rr, 1)}</td>`, `<td class="n">${num(r.ind.rsi)}</td>`, sig, earn, name,
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

const zone = (z) => (z && fin(z[0]) ? `${px(z[0])}~${px(z[1]).slice(1)}` : "–");
const swingTxt = (arr, n = 7) => (arr || []).slice(-n).map((p) => (p >= 100 ? p.toFixed(0) : p >= 10 ? p.toFixed(1) : p.toFixed(2))).join("·");

function boxSparkSvg(r) {
  const w = r.spark_w;
  const v = (w && w.v || []).filter(fin);
  if (v.length < 2) return "<svg></svg>";
  const b = r.box;
  const W = 300, H = 120, P = 5;
  const L = Math.log;
  let lo = Math.min(...v.map(L), L(b.low_zone[0]), L(r.stop)), hi = Math.max(...v.map(L), L(b.high_zone[1]));
  const span = hi - lo || 1;
  const t0 = Date.parse(w.start), t1 = Date.parse(w.end);
  const X = (i) => P + (i / (v.length - 1)) * (W - 2 * P);
  const XT = (d) => P + ((Date.parse(d) - t0) / (t1 - t0 || 1)) * (W - 2 * P);
  const Y = (y) => P + (1 - (L(y) - lo) / span) * (H - 2 * P);
  const band = (z, c) => `<rect x="0" y="${Y(z[1]).toFixed(1)}" width="${W}" height="${Math.max(1, Y(z[0]) - Y(z[1])).toFixed(1)}" fill="var(${c})" opacity=".14"/>`;
  const d = v.map((y, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(y).toFixed(1)}`).join("");
  const dots = (b.swings || []).filter((s) => s.ok && s.d >= w.start).map((s) =>
    `<circle cx="${XT(s.d).toFixed(1)}" cy="${Y(s.p).toFixed(1)}" r="2.6" fill="var(${s.k === "H" ? "--up" : "--down"})"/>`).join("");
  const ys = Y(r.stop);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="${esc(r.t)} 박스 ${b.years}년 주봉">
    ${band(b.high_zone, "--up")}${band(b.low_zone, "--down")}
    <line x1="0" x2="${W}" y1="${ys}" y2="${ys}" stroke="var(--critical)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>
    <path d="${d}" fill="none" stroke="var(--ink2)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>${dots}
    <circle cx="${X(v.length - 1)}" cy="${Y(v[v.length - 1])}" r="3.2" fill="var(--accent)"/></svg>`;
}

function sparkSvg(r) {
  if (r.box && r.spark_w) return boxSparkSvg(r);
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
    ${list === "box" ? `<div class="gf"><span class="note"><span class="up">고점 ${swingTxt(r.box.highs)}</span><br><span class="down">저점 ${swingTxt(r.box.lows)}</span></span></div>
    <div class="gf"><span class="note">${r.box.years}년 · 진폭 ${num(r.box.amp, 1)}배 · 왕복 ${num(r.box.round_trips, 1)}회 · 위치 ${num(r.box.pos * 100)}%</span><span class="note">RSI ${num(r.ind.rsi)}</span></div>`
    : `<div class="gf"><span class="note">매집 ${num(r.score, 2)} · 3개월 ${pct(r.ret3m, 0)}</span><span class="note">RSI ${num(r.ind.rsi)}</span></div>`}
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
    ? "쿠라 스시형: 1년 · 2~3년 · 4~5년 차트에서 큰 고점(빨강 구간)과 큰 저점(파랑 구간)을 3번 이상 오간 종목 중 지금 저점 구간 근처인 종목입니다 (예: 고점 100·120·90·100, 저점 50·60·40·40). 진폭 = 기준 고점 ÷ 기준 저점. 타이밍 점수는 저점 구간 근접·손익비·RSI·MACD·볼린저·스토캐스틱 RSI·슈퍼트렌드를 합친 값입니다. 소형주까지 포함하므로 필요하면 필터의 '시총'으로 거르세요."
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
    ${list === "box" && all.some((r) => r.boxes) ? `<div class="bp-row"><span class="note">박스 기간</span><span class="seg" id="bp">${[["all", "전체"], ...BOX_GROUPS].map(([k, t]) =>
      `<button data-bp="${k}" class="${S.filters.box.bp === k ? "on" : ""}">${t} <span class="note">${k === "all" ? all.length : all.filter((r) => r.boxes && r.boxes[k]).length}</span></button>`).join("")}</span>
      <span class="note">같은 종목도 기간마다 박스가 다를 수 있습니다 (1년 = 15% 이상 출렁임·진폭 1.4배↑, 2~3년 = 20~25%·1.6~1.8배↑, 4~5년 = 25%·1.8배↑)</span></div>` : ""}
    ${filterBar(list)}
    <p class="note" id="count">${rows.length}개 표시 · 기본 순서: 단계(매수 검토 → 관찰 → 대기) 다음 타이밍 점수 · 누르면 차트·지표·위험 내용 (← → 또는 좌우로 밀어 다음 종목)</p>
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
    const t = e.target.closest("[data-sort],[data-excl],[data-excl-clear],[data-v],[data-quick],[data-bp],tr[data-i],.gcard");
    if (!t) return;
    if (t.dataset.bp) { f.bp = t.dataset.bp; save(); }
    else if (t.dataset.sort) {
      const s = S.sort[list];
      if (s.k === t.dataset.sort) s.d = -s.d; else { s.k = t.dataset.sort; s.d = ["pos", "earn", "t", "risk"].includes(s.k) ? 1 : -1; }
      store.set("sort", S.sort); renderList(list);
    } else if (t.dataset.excl) {
      const c = t.dataset.excl;
      f.excl = f.excl.includes(c) ? f.excl.filter((x) => x !== c) : [...f.excl, c]; save();
    } else if (t.hasAttribute("data-excl-clear")) { f.excl = []; save(); }
    else if (t.dataset.v) { S.view[list] = t.dataset.v; store.set("view", S.view); renderList(list); }
    else if (t.dataset.quick === "buy") { Object.assign(f, DEFAULT_FILTERS, { bp: f.bp, stage: "매수 검토" }); save(); }
    else if (t.dataset.quick === "high") { Object.assign(f, DEFAULT_FILTERS, { bp: f.bp, risk: "only" }); save(); }
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
  S.cur = { kind: "list", list, rows, i };
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
  else if (S.tab === "similar") renderSim();
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
  if (S.cur.kind === "sim") return renderSimDetail();
  return renderListDetail();
}

function renderListDetail() {
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
      <div><div class="k">${list === "box" ? "목표 (가장 낮은 고점)" : "1년 고점"}</div><div class="v up">${px(r.target)}</div><div class="note">${pct(r.target / r.price - 1, 0)}</div></div>
    </div><p class="note" style="margin:6px 0 0">손익비 ${num(r.rr, 1)} = 목표까지 상승 폭 ÷ 손절까지 하락 폭. ${list === "box"
      ? "목표 = 지난 고점들 중 가장 낮은 고점 (매번 적어도 여기까지는 올라감). 손절가 = 지난 저점들 중 가장 낮은 저점 × 0.95 (여기를 깨면 박스가 깨진 것)."
      : "손절가 = 최근 20일 최저가 × 0.97."}</p>`;
  const sigs = r.signals.length ? r.signals.map((s) => `<li><span class="tone ${s.tone === "bull" ? "up" : s.tone === "bear" ? "down" : ""}">${s.tone === "bull" ? "▲" : s.tone === "bear" ? "▼" : "●"}</span><b>${esc(s.label)}</b> <span class="note">${esc(s.detail)}</span></li>`).join("") : `<li class="note">눈에 띄는 신호 없음</li>`;
  const flags = r.risk.flags.length ? r.risk.flags.map((f) => flagHtml(f, D.risk_categories)).join("") : `<li class="note">찾은 주의 신호가 없습니다. 그래도 매수 전 최근 공시·뉴스를 직접 확인하세요.</li>`;
  const news = r.news.length ? r.news.map((n) => `<li class="${n.flag === "high" || n.flag === "mid" ? "news-hit" : ""}"><span class="note">${esc(n.date.slice(5, 10))}</span> <a href="${safeUrl(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>${n.flag ? ` <span class="risk ${n.flag}"><span class="ic">${RISK_IC[n.flag]}</span></span>` : ""}</li>`).join("") : `<li class="note">최근 뉴스 없음</li>`;
  const analyst = (r.analyst || []).map((a) => `<li><span class="note">${esc(a.date.slice(5))}</span> ${esc(a.firm)} · ${esc(a.from ? a.from + " → " : "")}${esc(a.to)}${fin(a.pt) ? ` · 목표가 ${fin(a.pt_prior) ? px(a.pt_prior) + "→" : ""}${px(a.pt)}` : ""}</li>`).join("");
  const filings = (r.filings || []).map((f) => `<li><span class="note">${esc(f.date.slice(5))}</span> <a href="${safeUrl(f.url)}" target="_blank" rel="noopener">${esc(f.form)}</a> ${esc(f.items || "")}</li>`).join("");
  const info = list === "box"
    ? `<dt>기간 · 진폭 · 왕복</dt><dd>${r.box.years}년 · ${num(r.box.amp, 2)}배 · ${num(r.box.round_trips, 1)}회</dd>
       <dt class="up">고점 구간</dt><dd class="up">${zone(r.box.high_zone)}</dd>
       <dt class="down">저점 구간</dt><dd class="down">${zone(r.box.low_zone)}</dd>
       <dt>기준 고점 / 기준 저점</dt><dd>${px(r.box.hm)} / ${px(r.box.lm)}</dd>
       <dt>박스 위치</dt><dd>${num(r.box.pos * 100)}% <span class="note">(0 = 기준 저점, 100 = 기준 고점)</span></dd>
       <dt>박스 점수 · KRUS 실적 지문 차이</dt><dd>${num(r.score)} · ${num(r.krus, 2)}</dd>
       </dl><p class="note" style="margin:8px 0 2px">고점 (시간순)</p><p class="up" style="margin:0;font-variant-numeric:tabular-nums">${swingTxt(r.box.highs, 20) || "–"}</p>
       <p class="note" style="margin:8px 0 2px">저점 (시간순)</p><p class="down" style="margin:0 0 4px;font-variant-numeric:tabular-nums">${swingTxt(r.box.lows, 20) || "–"}</p><dl class="kv">`
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
    <div class="check-row"><button class="btn ${chk ? "primary" : ""}" id="chk-btn">${chk ? "✓ 오늘 확인함" : "확인 완료로 표시 (C)"}</button> ${last}
      <span class="note swipe-hint">· 차트 밖을 좌우로 밀면 다음 종목</span></div>
    <div class="cols">
      <div>
        <div class="chart-wrap">
          <div class="chart-tools">
            <span class="seg" id="periods">${(list === "box" ? [["BOX"]] : []).concat(PERIODS).map(([k]) => `<button data-p="${k}" class="${S.period[list] === k ? "on" : ""}">${k === "BOX" ? `박스 ${r.box.years}년` : k}</button>`).join("")}</span>
          </div>
          <details class="tools"${matchMedia("(max-width: 700px)").matches ? "" : " open"}><summary>지표 켜고 끄기</summary><div class="chart-tools">
            <div class="chips" id="ovs">${OVERLAYS.filter(([k]) => list === "box" || (k !== "swing")).map(([k, t, c]) => `<button class="chip${S.ov[k] ? " on" : ""}" data-ov="${k}"><span class="sw" style="background:var(${c})"></span>${t}</button>`).join("")}</div>
            <div class="chips" id="pns"><span class="note">아래 창:</span>${PANES.map(([k, t]) => `<button class="chip${S.panes[k] ? " on" : ""}" data-pn="${k}">${t}</button>`).join("")}</div>
          </div></details>
          <div class="chart-legend" id="legend"></div>
          <div class="chart" id="chart"><p class="note" style="padding:20px">차트 불러오는 중…</p></div>
        </div>
        <h3>지표 신호 (마지막 거래일 기준)</h3>
        <div class="card"><ul class="list">${sigs}</ul>
          <p class="note" style="margin:8px 0 0">RSI ${num(r.ind.rsi)} · 스토캐스틱 RSI ${num(r.ind.stoch_k)} · MFI ${num(r.ind.mfi)} · ADX ${num(r.ind.adx)} · 볼린저 %B ${num(r.ind.bb_pctb, 2)} ·
          50일선 대비 ${pct(r.ind.vs_sma50)} · 200일선 대비 ${pct(r.ind.vs_sma200)} · 하루 변동폭(ATR) ${pct(r.ind.atr_pct, 1, false)} · 슈퍼트렌드 ${r.ind.st_dir === 1 ? "상승" : r.ind.st_dir === -1 ? "하락" : "–"}</p></div>
        <h3>비슷한 차트들로 본 10거래일 뒤</h3>
        <div class="card" id="simcard">${D.similar && r.sim ? `<p class="note">불러오는 중…</p>` : `<p class="note">이 종목은 비슷한 차트 분석 대상이 아닙니다 (${scopeTxt()}만).</p>`}</div>
        <h3>지금 모양이 비슷한 종목</h3>
        <div class="card" id="shapecard"><p class="note">불러오는 중…</p></div>
        <h3>최근 뉴스</h3>
        <div class="card"><ul class="list">${news}</ul></div>
        ${analyst || filings ? `<h3>애널리스트 · 공시</h3><div class="card"><ul class="list">${analyst}${filings}</ul></div>` : ""}
      </div>
      <div>
        <h3 style="margin-top:4px">매수 주의</h3>
        <div class="card"><ul class="list">${flags}</ul>${D.sec_enabled ? "" : `<p class="note" style="margin:8px 0 0">SEC 공시(증자 신고서 등) 확인은 꺼져 있습니다. 켜는 방법은 '설명' 탭에 있습니다.</p>`}</div>
        <h3>타이밍 점수 ${num(r.timing)} / 100</h3>
        <div class="card parts">${parts}${plan}</div>
        <h3>${list === "box" ? `쿠라 스시형 박스 (${r.box.years}년)` : "매집 흔적"}</h3>
        <div class="card">${list === "box" && r.boxes && Object.keys(r.boxes).length > 1 ? `<div class="chips" id="bgs" style="margin:0 0 8px"><span class="note">이 종목의 박스:</span>${BOX_GROUPS.filter(([g]) => r.boxes[g]).map(([g, t]) =>
            `<button class="chip${(r.group || "") === g ? " on" : ""}" data-bg="${g}">${t} (${r.boxes[g].box.years}년)</button>`).join("")}</div>` : ""}<dl class="kv">${info}
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
  const bgs = $("#bgs");
  if (bgs) bgs.onclick = (e) => {
    const b = e.target.closest("[data-bg]");
    if (!b) return;
    const orig = S.data.lists.box.find((x) => x.t === r.t);
    S.cur.rows = S.cur.rows.slice();
    S.cur.rows[i] = boxRow(orig, b.dataset.bg);
    S.period.box = "BOX";
    renderDetail();
  };
  $("#memo").oninput = (e) => { S.memo[r.t] = e.target.value; store.set("memo", S.memo); };
  $("#periods").onclick = (e) => { const b = e.target.closest("[data-p]"); if (!b) return; S.period[list] = b.dataset.p; store.set("period." + list, b.dataset.p); renderDetail(); };
  $("#ovs").onclick = (e) => { const b = e.target.closest("[data-ov]"); if (!b) return; S.ov[b.dataset.ov] = !S.ov[b.dataset.ov]; store.set("ov", S.ov); drawChart(r, list); b.classList.toggle("on"); };
  $("#pns").onclick = (e) => { const b = e.target.closest("[data-pn]"); if (!b) return; S.panes[b.dataset.pn] = !S.panes[b.dataset.pn]; store.set("panes", S.panes); drawChart(r, list); b.classList.toggle("on"); };
  drawChart(r, list);
  if (D.similar && r.sim) simBlock(r.t, $("#simcard"), false);
  shapeBlock(r.t, $("#shapecard"), list === "box" ? (r.box.years >= 4 ? "5Y" : r.box.years >= 2 ? "3Y" : "1Y") : "1Y");
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
  let cd0;
  try { cd0 = await loadChart(r.t); } catch (e) { el.innerHTML = `<p class="note" style="padding:20px">${esc(e.message)}</p>`; return; }
  if (!S.cur || S.cur.rows[S.cur.i].t !== r.t) return; // 그 사이 다른 종목으로 넘어감
  // 박스 기간 묶음을 골랐으면 그 기간의 박스 (구간·고점·저점·시작일)
  const cd = { ...cd0, box: (list === "box" && r.group && cd0.boxes && cd0.boxes[r.group]) || cd0.box };
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
    timeScale: { borderColor: C("--line"), rightOffset: 4, lockVisibleTimeRangeOnResize: true, minBarSpacing: 0.05 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: "ko-KR" },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
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
      const hz = cd.box.high_zone, lz = cd.box.low_zone, dash = LW.LineStyle.Dashed;
      candle.createPriceLine({ price: hz[1], color: up, lineWidth: 1, lineStyle: dash, axisLabelVisible: false, title: "" });
      candle.createPriceLine({ price: hz[0], color: up, lineWidth: 2, lineStyle: dash, axisLabelVisible: true, title: "목표 (가장 낮은 고점)" });
      candle.createPriceLine({ price: lz[1], color: down, lineWidth: 1, lineStyle: dash, axisLabelVisible: false, title: "" });
      candle.createPriceLine({ price: lz[0], color: down, lineWidth: 2, lineStyle: dash, axisLabelVisible: true, title: "가장 낮은 저점" });
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
  if (S.ov.swing && cd.box) {
    for (const sw of cd.box.swings || []) {
      const t = sw.ok && snap(sw.d);
      if (!t) continue;
      const txt = sw.p >= 100 ? sw.p.toFixed(0) : sw.p >= 10 ? sw.p.toFixed(1) : sw.p.toFixed(2);
      const narrow = el.clientWidth < 520;  // 휴대폰: 화살표·색으로 고점/저점이 구분되므로 가격만
      markers.push(sw.k === "H"
        ? { time: t, position: "aboveBar", color: up, shape: "arrowDown", text: (narrow ? "" : "고 ") + txt }
        : { time: t, position: "belowBar", color: down, shape: "arrowUp", text: (narrow ? "" : "저 ") + txt });
    }
  }
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

  const per = S.period[list] || "1Y";
  let from;
  if (per === "BOX" && cd.box) from = Math.max(0, dates.findIndex((d) => d >= cd.box.start) - 5);
  else from = Math.max(0, dates.length - (PERIODS.find(([k]) => k === per) || PERIODS[2])[1]);
  chart.timeScale().setVisibleLogicalRange({ from, to: dates.length + 3 });

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

// ---------------------------------------------------------------- 비슷한 차트 (similarchart 방식)
const SIM_LS = [["all", "종합"], ["8", "8일"], ["16", "16일"], ["32", "32일"], ["64", "64일"], ["128", "128일"]];

function simVal(r, L) {
  if (L !== "all") { const v = r.s[L]; return v ? { score: v[0], rise: v[1], avg: v[2], n: v[3] } : null; }
  const vs = Object.values(r.s).filter((v) => v && fin(v[0]));
  if (!vs.length || !fin(r.total)) return null;
  const m = (k) => vs.reduce((a, v) => a + v[k], 0) / vs.length;
  return { score: r.total, rise: m(1), avg: m(2), n: Math.round(m(3)) };
}

async function loadSim() {
  if (!S.sim.data) {
    const res = await fetch("data/similar/summary.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("비슷한 차트 데이터가 아직 없습니다");
    S.sim.data = await res.json();
  }
  return S.sim.data;
}

async function loadSimFile(t) {
  if (S.sim.files.has(t)) return S.sim.files.get(t);
  const res = await fetch(`data/similar/${encodeURIComponent(t)}.json`);
  if (!res.ok) throw new Error(`${t}: 비슷한 차트 분석 대상이 아닙니다 (${scopeTxt()})`);
  const j = await res.json();
  S.sim.files.set(t, j);
  return j;
}

// ---------------------------------------------------------------- 종목 검색 (similarchart 처럼: 종목을 치면 바로 비슷한 차트)
function searchMatches(q) {
  const D = S.sim.data;
  if (!D) return [];
  const Q = q.trim().toUpperCase();
  if (!Q) {
    const by = new Map(D.rows.map((r) => [r.t, r]));
    return store.get("recent", []).map((t) => by.get(t)).filter(Boolean);
  }
  const hit = [];
  for (const r of D.rows) {
    const nm = (r.name || "").toUpperCase();
    const k = r.t === Q ? 0 : r.t.startsWith(Q) ? 1 : nm.startsWith(Q) ? 2 : r.t.includes(Q) ? 3 : nm.includes(Q) ? 4 : -1;
    if (k >= 0) hit.push([k, r]);
  }
  return hit.sort((a, b) => a[0] - b[0] || a[1].t.length - b[1].t.length || b[1].mcap - a[1].mcap).slice(0, 8).map((x) => x[1]);
}

function openSearched(r) {
  store.set("recent", [r.t, ...store.get("recent", []).filter((t) => t !== r.t)].slice(0, 8));
  openSim([r], 0);
}

// 입력칸 하나에 자동완성 목록을 붙인다 (상단 검색, 비슷한 차트 상세 안 검색)
function mountSearch(input, list) {
  let hits = [], at = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); at = -1; };
  const draw = () => {
    const q = input.value.trim();
    hits = searchMatches(q);
    if (at >= hits.length) at = hits.length - 1;
    const head = !q && hits.length ? `<li class="gs-head" role="presentation">최근 검색</li>` : "";
    list.innerHTML = hits.length
      ? head + hits.map((r, i) => `<li role="option" id="${list.id}-${i}" data-i="${i}" aria-selected="${i === at}" class="${i === at ? "on" : ""}">
          <b>${esc(r.t)}</b><span class="nm">${esc(r.name || "")}</span>
          <span class="sc ${r.total >= 6 ? "up" : r.total <= 4 ? "down" : ""}">${fin(r.total) ? num(r.total, 1) : "–"}</span></li>`).join("")
      : q ? `<li class="gs-none" role="presentation">'${esc(q)}' 없음 · ${scopeTxt()}만 분석합니다</li>`
        : `<li class="gs-none" role="presentation">티커나 회사 이름을 입력하세요. 오른쪽 숫자 = 비슷한 차트 점수 (5 = 평균)</li>`;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (at >= 0) input.setAttribute("aria-activedescendant", `${list.id}-${at}`); else input.removeAttribute("aria-activedescendant");
  };
  const ready = async () => {
    if (!S.data || !S.data.similar) { list.innerHTML = `<li class="gs-none">비슷한 차트 데이터는 다음 자동 실행 때부터 생깁니다.</li>`; list.hidden = false; return false; }
    if (!S.sim.data) {
      list.innerHTML = `<li class="gs-none">불러오는 중…</li>`; list.hidden = false;
      try { await loadSim(); } catch (e) { list.innerHTML = `<li class="gs-none">${esc(e.message)}</li>`; return false; }
    }
    return true;
  };
  const pick = (r) => { input.value = ""; close(); input.blur(); openSearched(r); };
  input.addEventListener("focus", async () => { if (await ready() && document.activeElement === input) draw(); });
  input.addEventListener("input", async () => { at = -1; if (await ready()) draw(); });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; close(); input.blur(); return; }
    if (list.hidden || !hits.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      at = (at + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length;
      draw();
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(hits[Math.max(at, 0)]);
    }
  });
  list.addEventListener("mousedown", (e) => e.preventDefault());  // 목록 누를 때 입력칸 blur 로 닫히지 않게
  list.addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) pick(hits[+li.dataset.i]); });
}

function simTable(rows, L, offset = 0) {
  if (!rows.length) return `<div class="card empty">없음</div>`;
  const SH = S.shpData;
  return `<div class="tbl"><table><thead><tr><th>#</th><th class="l">종목</th>${SH ? `<th class="l">6개월</th>` : ""}<th>점수</th><th>상승 비율</th><th>10일 뒤 평균</th>
    <th>현재가 · 1일</th><th>시총</th><th class="l">이름</th></tr></thead><tbody>${rows.map((r, i) => {
      const v = r.v;
      return `<tr data-si="${i + offset}"><td class="n">${i + 1}</td><td class="l tk"><b>${esc(r.t)}</b>${r.box ? `<span class="badge both">박스</span>` : ""}${r.accum ? `<span class="badge both">매집</span>` : ""}</td>
        ${SH ? `<td class="thumb">${SH.shapes["6M"] && SH.shapes["6M"][r.t] ? shapeSvg(SH, SH.shapes["6M"][r.t], null, { w: 132, h: 44, cls: "th" }) : ""}</td>` : ""}
        <td>${meter(v.score * 10).replace(/<b>[^<]*<\/b>/, `<b>${num(v.score, 1)}</b>`)}</td>
        <td class="n">${num(v.rise * v.n)}/${v.n} <span class="note">(${num(v.rise * 100)}%)</span></td><td class="n">${pctC(v.avg)}</td>
        <td class="n">${px(r.price)} ${pctC(r.chg1d)}</td><td class="n">${money(r.mcap)}</td><td class="l wrap">${esc(r.name)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

async function renderSim() {
  const main = $("#main");
  main.onclick = main.oninput = main.onchange = null;
  if (!S.data.similar) { main.innerHTML = `<div class="card">비슷한 차트 데이터는 다음 자동 실행 때부터 생깁니다.</div>`; return; }
  if (!S.sim.data) main.innerHTML = `<p class="note">불러오는 중…</p>`;
  let D;
  try { D = await loadSim(); } catch (e) { main.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
  if (!S.shpData) { try { S.shpData = await loadShapes(); } catch { /* 모양 파일이 없으면 썸네일 없이 */ } }
  if (S.tab !== "similar") return;
  const L = S.sim.L, q = S.sim.q.trim().toUpperCase();
  const all = D.rows.map((r) => ({ ...r, v: simVal(r, L) })).filter((r) => r.v && fin(r.v.score));
  const mc = S.sim.mcap;
  const shown = all.filter((r) => (mc === "all" || (mc === "small" ? r.mcap < 2e9 : r.mcap >= 2e9))
    && (!q || r.t.includes(q) || (r.name || "").toUpperCase().includes(q)));
  const up = [...shown].sort((a, b) => b.v.score - a.v.score);
  const exact = q ? up.filter((r) => r.t === q) : [];
  const top = up.slice(0, 30), bottom = [...shown].sort((a, b) => a.v.score - b.v.score).slice(0, 15);
  const base = D.bases[L === "all" ? "32" : L];
  const mood = all.reduce((a, r) => a + r.v.score, 0) / (all.length || 1);
  S.sim.lists = { exact, top, bottom };
  main.innerHTML = `
    <p class="note" style="margin:2px 0 8px">지금 차트(최근 8~128거래일)와 가장 닮은 <b>과거 차트 ${D.k}개</b>를 미국 ${all.length.toLocaleString()}개 종목의 지난 5년에서 찾고,
      그 차트들이 <b>${D.horizon}거래일 뒤</b> 어떻게 됐는지 봅니다 (similarchart.com 방식). 점수 5 = 과거 차트 전체 평균, 높을수록 비슷한 차트들이 많이 올랐다는 뜻입니다.
      <b>맨 위 검색칸</b>에 아무 종목이나 치면 (어느 탭에서든, 단축키 /) 그 종목의 비슷한 차트가 바로 열립니다.</p>
    <div class="tiles">
      <div class="tile"><div class="k">시장 분위기 (평균 점수)</div><div class="v">${num(mood, 2)}</div></div>
      <div class="tile"><div class="k">상승 예상 (점수 6 이상)</div><div class="v">${all.filter((r) => r.v.score >= 6).length}</div></div>
      <div class="tile"><div class="k">하락 예상 (점수 4 이하)</div><div class="v">${all.filter((r) => r.v.score <= 4).length}</div></div>
      <div class="tile"><div class="k">과거 차트 전체 ${D.horizon}일 뒤 상승 확률</div><div class="v">${num(base.rise * 100)}%</div></div>
    </div>
    <div class="filters">
      <span class="seg" id="simL">${SIM_LS.map(([k, t]) => `<button data-l="${k}" class="${L === k ? "on" : ""}">${t}</button>`).join("")}</span>
      <input type="search" id="simq" placeholder="종목 검색 (예: KRUS)" value="${esc(S.sim.q)}">
      <label>시총 <select id="simmc">${[["all", "전체"], ["small", "$20억 미만"], ["large", "$20억 이상"]].map(([k, t]) => `<option value="${k}"${mc === k ? " selected" : ""}>${t}</option>`).join("")}</select></label>
      <span class="note">기준일 ${esc(D.asof)}</span>
    </div>
    ${exact.length ? `<h2>검색한 종목</h2>${simTable(exact, L, 0)}` : q && !shown.length ? `<div class="card note">'${esc(q)}' 를 찾지 못했습니다. ${scopeTxt()}만 분석합니다.</div>` : ""}
    <h2>상승 예상 순위 <span class="note">(${L === "all" ? "8~128일 종합" : L + "거래일 차트"})</span></h2>${simTable(top, L, 1000)}
    <h2>하락 예상 순위</h2>${simTable(bottom, L, 2000)}
    <p class="note">과거에 비슷한 모양이 그 뒤 어떻게 됐는지일 뿐, 이번에도 같을 거라는 보장은 없습니다. 검증 결과는 '설명' 탭에 있습니다.</p>`;
  $("#simL").onclick = (e) => { const b = e.target.closest("[data-l]"); if (!b) return; S.sim.L = b.dataset.l; store.set("simL", S.sim.L); if (S.sim.L !== "all") { S.sim.dL = S.sim.L; store.set("simDL", S.sim.dL); } renderSim(); };
  $("#simq").oninput = (e) => { S.sim.q = e.target.value; const pos = e.target.selectionStart; renderSim().then(() => { const q2 = $("#simq"); if (q2) { q2.focus(); q2.setSelectionRange(pos, pos); } }); };
  $("#simmc").onchange = (e) => { S.sim.mcap = e.target.value; renderSim(); };
  main.onclick = (e) => {
    const tr = e.target.closest("tr[data-si]");
    if (!tr) return;
    const k = +tr.dataset.si;
    const [rows, i] = k >= 2000 ? [bottom, k - 2000] : k >= 1000 ? [top, k - 1000] : [exact, k];
    openSim(rows, i);
  };
}

function openSim(rows, i) {
  S.cur = { kind: "sim", rows, i };
  $("#drawer").hidden = false;
  document.body.style.overflow = "hidden";
  renderDetail();
}

async function renderSimDetail() {
  const { rows, i } = S.cur;
  const t = rows[i].t;
  history.replaceState(null, "", `#similar/${t}`);
  $("#d-pos").textContent = rows.length > 1 ? `비슷한 차트 ${i + 1} / ${rows.length}` : "비슷한 차트";
  const inList = ["box", "accum"].map((k) => [k, S.data.lists[k].findIndex((r) => r.t === t)]).filter(([, j]) => j >= 0);
  $("#d-body").innerHTML = `<div class="dh"><div><h2 id="d-title">${esc(t)}</h2><div class="note" id="sim-name"></div></div>
      <span class="grow"></span><div class="links">${inList.map(([k]) => `<a href="#" data-golist="${k}">${LISTS[k]} 상세 →</a>`).join("")}
      <a href="https://finance.yahoo.com/quote/${encodeURIComponent(t)}" target="_blank" rel="noopener">야후</a>
      <a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(t)}" target="_blank" rel="noopener">트레이딩뷰</a></div></div>
    <div class="gsearch in-detail" role="search">
      <input type="search" id="dq" placeholder="다른 종목 검색" autocomplete="off" autocapitalize="characters" spellcheck="false" enterkeyhint="search"
        role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dq-list" aria-label="다른 종목 검색">
      <ul class="gs-list" id="dq-list" role="listbox" hidden></ul>
    </div>
    <div class="card" id="simblock"><p class="note">불러오는 중…</p></div>
    <h3>지금 모양이 비슷한 종목 <span class="note">(6개월 · 1년 · 3년 · 5년 차트)</span></h3>
    <div class="card" id="shapeblock"><p class="note">불러오는 중…</p></div>`;
  mountSearch($("#dq"), $("#dq-list"));
  shapeBlock(t, $("#shapeblock"), "1Y");
  $("#d-body").querySelectorAll("[data-golist]").forEach((a) => a.onclick = (e) => {
    e.preventDefault();
    const k = a.dataset.golist;
    openDetail(k, S.data.lists[k], S.data.lists[k].findIndex((r) => r.t === t));
  });
  await simBlock(t, $("#simblock"), true);
}

async function simBlock(t, el, full) {
  let f;
  try { f = await loadSimFile(t); } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  if (!el.isConnected) return;
  const ls = Object.keys(f.w).sort((a, b) => a - b);
  if (!ls.length) { el.innerHTML = `<p class="note">비슷한 차트를 충분히 찾지 못했습니다.</p>`; return; }
  const L = f.w[S.sim.dL] ? S.sim.dL : ls.includes("32") ? "32" : ls[0];
  const w = f.w[L];
  const nm = $("#sim-name");
  if (nm) nm.textContent = f.name || "";
  const up = Math.round(w.rise * w.n);
  el.innerHTML = `
    <div class="tbl" style="border:0"><table><thead><tr><th class="l">차트 길이</th><th>점수</th><th>상승 비율</th><th>10일 뒤 평균</th><th>중간값</th><th>평균 유사도</th></tr></thead>
      <tbody>${ls.map((k) => { const x = f.w[k]; return `<tr data-dl="${k}" class="${k === L ? "sel" : ""}"><td class="l">${k}거래일${k === L ? " ◀" : ""}</td><td class="n"><b>${num(x.score, 1)}</b></td>
        <td class="n">${Math.round(x.rise * x.n)}/${x.n} (${num(x.rise * 100)}%)</td><td class="n">${pctC(x.avg)}</td><td class="n">${pctC(x.med)}</td><td class="n">${num(x.sim * 100)}%</td></tr>`; }).join("")}</tbody></table></div>
    <p style="margin:10px 0 4px"><b>${L}거래일</b> 차트와 비슷한 과거 차트 <b>${w.n}개</b> 중 <b class="up">${up}개 (${num(w.rise * 100)}%)</b>가 10거래일 뒤 올랐고, 평균 ${pctC(w.avg)} · 중간값 ${pctC(w.med)} 움직였습니다. 점수 <b>${num(w.score, 1)}</b>.</p>
    ${fanSvg(w, +L)}
    <p class="note" style="margin:4px 0 8px">굵은 검은 선 = 지금 ${esc(t)} 차트 · 회색 = 비슷한 과거 차트 ${w.top.length}개 · 그 뒤 10일: <span class="up">빨강 = 오름</span>, <span class="down">파랑 = 내림</span> ·
      보라 띠 = 비슷한 차트 ${w.n}개의 10일 뒤 범위 (진한 띠 가운데 50%, 연한 띠 80%), 보라 선 = 중간값. 모든 선은 지금(오늘) 가격 = 0% 로 맞춤.</p>
    ${full ? `<h3>가장 닮은 과거 차트 ${w.top.length}개</h3>
      <p class="note" style="margin:0 0 6px">검은 선 = 그 종목의 과거 차트, 주황 점선 = 지금 ${esc(t)} 차트 (겹쳐 비교), 오른쪽 = 그 뒤 10거래일 (<span class="up">빨강 오름</span> / <span class="down">파랑 내림</span>).</p>
      <div class="analogs">${w.top.map((a) => `<a class="analog" href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(a.t)}" target="_blank" rel="noopener" title="${esc(a.t)} 차트 열기">
        <div class="ah"><b>${esc(a.t)}</b><span class="note">${esc(shortD(a.start))} ~ ${esc(shortD(a.end))}</span></div>
        ${analogSvg(a, w.now)}
        <div class="af"><span class="note">유사도 ${num(a.s * 100)}%</span><span>10일 뒤 ${pctC(a.r)}</span></div></a>`).join("")}</div>` : `<p class="note"><a href="#similar/${encodeURIComponent(t)}" data-simopen>비슷한 과거 차트 목록 보기 →</a></p>`}`;
  el.querySelectorAll("tr[data-dl]").forEach((tr) => tr.onclick = () => { S.sim.dL = tr.dataset.dl; store.set("simDL", S.sim.dL); simBlock(t, el, full); });
  const so = el.querySelector("[data-simopen]");
  if (so) so.onclick = (e) => { e.preventDefault(); openSim([{ t }], 0); };
}

const shortD = (d) => String(d || "").slice(2).replace(/-/g, ".");

// 과거 차트 하나: 그 구간 모양 + 지금 차트 겹침 + 그 뒤 10일
function analogSvg(a, now) {
  const W = 220, H = 110, P = { l: 4, r: 4, t: 6, b: 6 };
  const fx = [0, ...a.f.map((_, i) => i + 1)], fv = [1, ...a.f];
  const ys = [...a.w, ...a.f, ...now.w];
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.06 || 0.02;
  lo -= pad; hi += pad;
  const x0 = Math.min(a.x[0], now.x[0]), x1 = fx[fx.length - 1];
  const X = (x) => P.l + ((x - x0) / (x1 - x0)) * (W - P.l - P.r);
  const Y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const line = (xs, vs) => xs.map((x, i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(vs[i]).toFixed(1)}`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="${esc(a.t)} 과거 차트와 그 뒤 10거래일">
    <line x1="${P.l}" x2="${W - P.r}" y1="${Y(1).toFixed(1)}" y2="${Y(1).toFixed(1)}" stroke="var(--grid)"/>
    <line x1="${X(0).toFixed(1)}" x2="${X(0).toFixed(1)}" y1="${P.t}" y2="${H - P.b}" stroke="var(--line)" stroke-dasharray="3 3"/>
    <path d="${line(now.x, now.w)}" fill="none" stroke="var(--s-orange)" stroke-width="1.4" stroke-dasharray="4 3" opacity=".9"/>
    <path d="${line(a.x, a.w)}" fill="none" stroke="var(--ink)" stroke-width="1.7"/>
    <path d="${line(fx, fv)}" fill="none" stroke="var(${a.r >= 0 ? "--up" : "--down"})" stroke-width="2"/>
  </svg>`;
}

function fanSvg(w, L) {
  const H0 = w.fan.p50.length;
  const narrow = matchMedia("(max-width: 600px)").matches;  // 휴대폰: 좁은 캔버스로 글씨를 키움
  const W = narrow ? 380 : 640, H = narrow ? 260 : 300, P = { l: 42, r: 10, t: 12, b: 28 };
  const ys = [...w.now.w, ...w.fan.p10, ...w.fan.p90, 1];
  for (const a of w.top) ys.push(...a.w, ...a.f);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.06 || 0.02;
  lo -= pad; hi += pad;
  const x0 = -(L - 1), x1 = H0;
  const X = (x) => P.l + ((x - x0) / (x1 - x0)) * (W - P.l - P.r);
  const Y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const line = (xs, vs) => xs.map((x, i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(vs[i]).toFixed(1)}`).join("");
  const fx = [0, ...Array.from({ length: H0 }, (_, i) => i + 1)];
  const band = (a, b) => `M${fx.map((x, i) => `${X(x).toFixed(1)},${Y(i ? w.fan[a][i - 1] : 1).toFixed(1)}`).join("L")}L${fx.slice().reverse().map((x) => { const i = fx.indexOf(x); return `${X(x).toFixed(1)},${Y(i ? w.fan[b][i - 1] : 1).toFixed(1)}`; }).join("L")}Z`;
  const span = hi - lo, stp = span > 0.8 ? 0.2 : span > 0.4 ? 0.1 : span > 0.16 ? 0.05 : 0.02;
  let grid = "";
  for (let v = Math.ceil((lo - 1) / stp) * stp; v <= hi - 1 + 1e-9; v += stp) {
    const y = Y(1 + v).toFixed(1);
    grid += `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="var(--grid)"/><text x="${P.l - 6}" y="${+y + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${v > 0 ? "+" : ""}${Math.round(v * 100)}%</text>`;
  }
  const analogs = w.top.map((a) => `<path d="${line(a.x, a.w)}" fill="none" stroke="var(--muted)" stroke-width="1.2" opacity=".45"/>
    <path d="${line([0, ...a.f.map((_, i) => i + 1)], [1, ...a.f])}" fill="none" stroke="var(${a.r >= 0 ? "--up" : "--down"})" stroke-width="1.3" opacity=".75"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="지금 차트와 비슷한 과거 차트, 그 뒤 10거래일">
    ${grid}
    <line x1="${X(0)}" x2="${X(0)}" y1="${P.t}" y2="${H - P.b}" stroke="var(--ink2)" stroke-dasharray="4 4"/>
    <path d="${band("p10", "p90")}" fill="var(--s-violet)" opacity=".10"/><path d="${band("p25", "p75")}" fill="var(--s-violet)" opacity=".20"/>
    ${analogs}
    <path d="${line(fx, [1, ...w.fan.p50])}" fill="none" stroke="var(--s-violet)" stroke-width="2.6"/>
    <path d="${line(w.now.x, w.now.w)}" fill="none" stroke="var(--ink)" stroke-width="2.6"/>
    <text x="${X(x0)}" y="${H - 8}" font-size="11" fill="var(--muted)">${L - 1}거래일 전</text>
    <text x="${X(0)}" y="${H - 8}" font-size="11" fill="var(--ink2)" text-anchor="middle">오늘</text>
    <text x="${X(x1)}" y="${H - 8}" font-size="11" fill="var(--muted)" text-anchor="end">${H0}거래일 뒤</text>
  </svg>`;
}

// ---------------------------------------------------------------- 지금 모양이 비슷한 종목 (6개월 · 1년 · 3년 · 5년)
const SHAPE_PS = [["6M", "6개월"], ["1Y", "1년"], ["3Y", "3년"], ["5Y", "5년"]];

async function loadShapes() {
  if (!S.shp) {
    S.shp = fetch("data/similar/shapes.json").then((res) => {
      if (!res.ok) throw new Error("모양 데이터가 아직 없습니다 (다음 자동 실행 때 생김)");
      return res.json();
    }).then((j) => ({ ...j, vec: {} })).catch((e) => { S.shp = null; throw e; });
  }
  return S.shp;
}

const shapeVals = (D, code) => Array.from(code, (ch) => D.chars.indexOf(ch));

// 기간 p 의 모든 종목 모양 → 평균 0 · 길이 1 벡터 (내적 = 상관계수)
function shapeVecs(D, p) {
  if (D.vec[p]) return D.vec[p];
  const tk = Object.keys(D.shapes[p] || {}), n = D.points;
  const m = new Float32Array(tk.length * n);
  tk.forEach((t, j) => {
    const v = shapeVals(D, D.shapes[p][t]);
    const mu = v.reduce((a, x) => a + x, 0) / n;
    const sd = Math.sqrt(v.reduce((a, x) => a + (x - mu) ** 2, 0)) || 1;
    for (let i = 0; i < n; i++) m[j * n + i] = (v[i] - mu) / sd;
  });
  return (D.vec[p] = { tk, m, idx: new Map(tk.map((t, j) => [t, j])) });
}

function shapeMatches(D, t, p, boxOnly, k = 12) {
  const V = shapeVecs(D, p), n = D.points, q = V.idx.get(t);
  if (q === undefined) return null;
  const want = boxOnly ? new Set(D.groups && p !== "6M" ? (p === "1Y" ? ["1"] : p === "3Y" ? ["3"] : ["5"]) : ["1", "3", "5"]) : null;
  const out = [];
  for (let j = 0; j < V.tk.length; j++) {
    if (j === q) continue;
    if (want && !(D.box[V.tk[j]] || []).some((g) => want.has(g))) continue;
    let s = 0;
    for (let i = 0; i < n; i++) s += V.m[q * n + i] * V.m[j * n + i];
    out.push([s, V.tk[j]]);
  }
  return out.sort((a, b) => b[0] - a[0]).slice(0, k);
}

// 모양 그림: 종목 모양(실선) + 비교할 모양(주황 점선, 같은 높이로 맞춤)
function shapeSvg(D, code, cmp, opt = {}) {
  const W = opt.w || 220, H = opt.h || 80, P = 4;
  const line = (c) => {
    const v = shapeVals(D, c), n = v.length;
    return v.map((y, i) => `${i ? "L" : "M"}${(P + (i / (n - 1)) * (W - 2 * P)).toFixed(1)},${(P + (1 - y / 63) * (H - 2 * P)).toFixed(1)}`).join("");
  };
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" ${opt.cls ? `class="${opt.cls}"` : `style="display:block;width:100%;height:auto"`} aria-hidden="true">
    ${cmp ? `<path d="${line(cmp)}" fill="none" stroke="var(--s-orange)" stroke-width="1.3" stroke-dasharray="4 3" opacity=".9" vector-effect="non-scaling-stroke"/>` : ""}
    <path d="${line(code)}" fill="none" stroke="var(--ink)" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

const boxTag = (D, t) => (D.box[t] || []).map((g) => `<span class="badge both">박스 ${groupLabel(g)}</span>`).join("");

async function shapeBlock(t, el, p0) {
  let D;
  try { D = await loadShapes(); } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  if (!el.isConnected) return;
  const st = S.shape || (S.shape = { p: null, box: store.get("shapeBox", false) });
  const ps = SHAPE_PS.filter(([k]) => D.shapes[k] && D.shapes[k][t]);
  if (!ps.length) { el.innerHTML = `<p class="note">${esc(t)}: 모양 비교 대상이 아닙니다 (${scopeTxt()}만, 기간만큼 거래 기록 필요).</p>`; return; }
  const p = ps.some(([k]) => k === st.p) ? st.p : ps.some(([k]) => k === p0) ? p0 : ps[ps.length - 1][0];
  const hits = shapeMatches(D, t, p, st.box) || [];
  const me = D.shapes[p][t];
  el.innerHTML = `
    <div class="filters" style="margin:0 0 8px">
      <span class="seg" data-sp>${ps.map(([k, l]) => `<button data-p="${k}" class="${k === p ? "on" : ""}">${l}</button>`).join("")}</span>
      <label><input type="checkbox" data-sbox${st.box ? " checked" : ""}> 박스형 종목만${p === "6M" ? "" : ` (${p === "1Y" ? "1년" : p === "3Y" ? "2~3년" : "4~5년"} 박스)`}</label>
    </div>
    <div class="shape-me">${shapeSvg(D, me, null, { h: 60 })}<span class="note">지금 ${esc(t)} 최근 ${SHAPE_PS.find(([k]) => k === p)[1]} (주봉 수준으로 줄인 모양)</span></div>
    ${hits.length ? `<div class="analogs">${hits.map(([s, u]) => `<a class="analog" href="#similar/${encodeURIComponent(u)}" data-shape="${esc(u)}">
        <div class="ah"><b>${esc(u)}</b><span class="note">모양 일치 ${num(s * 100)}%</span></div>
        ${shapeSvg(D, D.shapes[p][u], me)}
        <div class="af"><span class="note nm1">${esc(D.names[u] || "")}</span></div>${boxTag(D, u) ? `<div class="af tags">${boxTag(D, u)}</div>` : ""}</a>`).join("")}</div>`
      : `<p class="note">조건에 맞는 종목이 없습니다.</p>`}
    <p class="note" style="margin:6px 0 0">검은 선 = 그 종목의 같은 기간 모양, 주황 점선 = 지금 ${esc(t)}. 모양 일치 = 가격 수준·변동 크기와 상관없는 모양의 상관계수입니다. 큰 오르내림의 흐름과 시기가 맞아야 높고, 긴 기간에서는 전체 흐름(올랐다 내림 등)이 크게 작용합니다.
      '고점·저점이 같은 구간에서 되풀이'되는 종목을 찾으려면 박스 하단권 탭의 '박스 기간'을 쓰거나 아래 '박스형 종목만'을 켜세요.
      박스형만 켜면 쿠라 스시형 박스 조건을 통과한 종목(저점 근처가 아니어도)만 봅니다.</p>`;
  el.querySelector("[data-sp]").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) { st.p = b.dataset.p; shapeBlock(t, el, p0); } };
  el.querySelector("[data-sbox]").onchange = (e) => { st.box = e.target.checked; store.set("shapeBox", st.box); shapeBlock(t, el, p0); };
  el.querySelectorAll("[data-shape]").forEach((a) => a.onclick = (e) => {
    e.preventDefault();
    const u = a.dataset.shape, j = S.data.lists.box.findIndex((x) => x.t === u);
    if (j >= 0) openDetail("box", S.data.lists.box, j);
    else openSearched((S.sim.data && S.sim.data.rows.find((x) => x.t === u)) || { t: u });
  });
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
    <h3>쿠라 스시형 박스란</h3>
    <p>고점과 저점이 <b>완전히 같을 필요는 없고, 비슷한 구간에서 되풀이</b>되는 모양입니다.
      예) 고점 100 → 저점 50 → 고점 120 → 저점 60 → 고점 90 → 저점 40 → 고점 100 → 저점 40: 고점은 90~120 (고점 구간), 저점은 40~60 (저점 구간).</p>
    <ul><li>지난 <b>4~5년 · 2~3년 · 1년</b> 일봉에서 따로 찾습니다. 4~5년은 25% 이상 되돌린 고점·저점, 진폭 1.8배 이상 / 2~3년은 20~25%, 1.6~1.8배 / 1년은 15%, 1.4배 이상.
        지금 진행 중인 움직임도 기준 이상이면 셉니다. 목록 위 '박스 기간' 버튼으로 기간별로 보고, 상세에서 같은 종목의 다른 기간 박스로 바꿔 볼 수 있습니다.</li>
      <li>기준 고점 = 고점 구간에 닿은 고점들의 중앙값, 기준 저점 = 저점들의 중앙값. <b>진폭</b> = 기준 고점 ÷ 기준 저점, <b>1.8배 이상</b>이어야 합니다.</li>
      <li>저점 구간 ↔ 고점 구간을 <b>3번 이상 왕복</b>해야 하고, 한 번 오갈 때 박스 높이의 절반 이상 움직여야 셉니다.</li>
      <li>고점들이나 저점들이 시간이 갈수록 한쪽으로 계속 올라가거나 내려가면(추세) 제외. 85% 이상의 날을 박스 근처에서 보내야 합니다.</li>
      <li>차트에서 빨강 ▼ = 고점, 파랑 ▲ = 저점, 빨강 점선 = 고점 구간, 파랑 점선 = 저점 구간입니다. 이 중 지금 저점 구간 근처(박스 위치 −30%~35%)인 종목만 목록에 나옵니다.</li></ul>
    <h3>비슷한 차트 (similarchart.com 방식)</h3>
    <ul><li>각 종목의 최근 8 · 16 · 32 · 64 · 128거래일 차트와 <b>가장 닮은 과거 차트 50개</b>를 ${scopeTxt()} (약 ${(S.data.universe.box || 0).toLocaleString()}개)의 지난 5년에서 찾습니다.
        닮음 = 가격 수준·변동 크기와 상관없는 모양의 상관계수, 하루 변동 크기가 0.5~2배인 차트만, 같은 종목의 겹치는 구간 제외, 한 종목에서 최대 2개.</li>
      <li><b>종목 검색</b>: 맨 위 검색칸에 티커나 회사 이름 (예: KRUS, Kura) → 지금 차트, 비슷한 과거 차트 50개의 10일 뒤 범위, 가장 닮은 과거 차트 6개를 하나씩 그림으로 보여줍니다.
        주소 <code>#similar/KRUS</code> 로 바로 열 수도 있습니다.</li>
      <li><b>지금 모양이 비슷한 종목</b>: 상세 화면 아래에서 최근 6개월 · 1년 · 3년 · 5년 차트 모양이 가장 닮은 종목 12개를 그림과 함께 보여줍니다
        (예: KRUS 5년 차트와 닮은 종목). '박스형 종목만'을 켜면 쿠라 스시형 박스 조건을 통과한 종목 중에서만 찾습니다.</li>
      <li>그 50개가 <b>10거래일 뒤</b> 오른 비율(상승 비율)과 평균 등락으로 <b>점수 0~10</b>을 매깁니다. 5 = 과거 차트 전체 평균, 6 이상 = 상승 예상, 4 이하 = 하락 예상.
        '종합'은 다섯 길이 점수의 평균입니다.</li>
      <li><b>과거 검증</b> (2024.6~2026.9, 28개 시점 × 250종목, 시총 $1.5억 · 주가 $3 이상 종목 기준, 그날까지 결과가 알려진 차트만 사용): 종합 점수 6 이상은 10거래일 뒤 <b>60.8%</b>가 올랐습니다
        (전체 54.6%, 같은 날 평균보다 +1.2%p, 324건). 32거래일 차트 점수 6 이상은 57.5% (+2.2%p). 하지만 점수 전체의 순위 상관은 거의 0 (IC +0.01)이고
        8 · 16 · 64일 점수는 차이가 없었습니다. <b>약한 참고 신호</b>로만 보세요.</li></ul>
    <h3>단계</h3>
    <ul><li><b>매수 검토</b>: 타이밍 점수 60 이상이고 위험이 '높음'이 아닌 종목</li><li><b>관찰</b>: 타이밍 40 이상</li><li><b>대기</b>: 그 밖</li></ul>
    <h3>타이밍 점수 (100점)</h3>
    <ul><li><b>박스 하단권</b>: ${w(W.box)}. 손익비 = (목표 − 현재가) ÷ (현재가 − 손절가). 목표 = 지난 고점들 중 가장 낮은 고점, 손절가 = 지난 저점들 중 가장 낮은 저점 × 0.95.</li>
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
      <li><b>쿠라 스시형 박스 하단 과거 검증</b> (2023.10~2026.6, 2개월마다 17개 시점, 그날까지의 데이터로 박스를 다시 찾음, 399건, 시총 $1.5억 · 주가 $3 이상 종목 기준):
        저점 구간 근처 종목은 <b>3개월 뒤 같은 날 전체 종목 평균보다 +5.2%p</b> 높았습니다 (17개 시점 중 71%, t≈2.3). 1개월 뒤는 +1.7%p 로 확실하지 않습니다.
        시점이 17개뿐이고 현재 상장된 종목만으로 계산했다는 한계가 있습니다.</li>
      <li>매집 흔적(거래량 쏠림·CMF 등)은 2022.11~2026.6 검증에서 이후 수익률을 예측하지 못했습니다. 소형주는 증자·급등락 위험이 특히 큽니다.</li>
      <li><b>타이밍 점수 과거 검증</b> (같은 17개 시점): 박스 하단 후보 안에서 타이밍 점수는 순서를 거의 가르지 못했습니다
        (상위 절반 − 하위 절반: 1개월 +0.9%p, 3개월 −0.2%p). 박스 하단에 있다는 것 자체가 더 중요했습니다.
        매집 흔적의 타이밍 점수도 이후 수익률을 예측하지 못했습니다 (60 이상 3개월 −2.0%). '성과 추적' 탭에 실제 결과가 쌓입니다.</li></ul>
    <p class="note">데이터: 야후 파이낸스(주가·뉴스·애널리스트·재무), SEC EDGAR(공시). 차트: TradingView Lightweight Charts.</p>
  </div>`;
  $("#main").onclick = $("#main").oninput = $("#main").onchange = null;
}

// ---------------------------------------------------------------- 탭·테마·시작
function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  if (LISTS[tab]) { renderList(tab); bindList(tab); }
  else if (tab === "similar") renderSim();
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
    if (S.cur && !$("#drawer").hidden) renderDetail();
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
  mountSearch($("#gq"), $("#gq-list"));
  $("#main").addEventListener("toggle", (e) => { if (e.target.matches("details.fwrap")) S.fopen = e.target.open; }, true);
  document.querySelector(".tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { history.replaceState(null, "", "#" + b.dataset.tab); setTab(b.dataset.tab); } };
  $("#drawer").onclick = (e) => {
    if (e.target.closest("[data-close]")) closeDetail();
    const nb = e.target.closest("[data-nav]");
    if (nb) navDetail(+nb.dataset.nav);
  };
  let touch = null;
  $("#d-body").addEventListener("touchstart", (e) => {
    touch = e.target.closest(".chart-wrap, textarea, .tbl") ? null : { x: e.touches[0].clientX, y: e.touches[0].clientY };
  }, { passive: true });
  $("#d-body").addEventListener("touchend", (e) => {
    if (!touch) return;
    const dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
    touch = null;
    if (Math.abs(dx) > 70 && Math.abs(dy) < 50) navDetail(dx < 0 ? 1 : -1);
  }, { passive: true });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) {
      const q = $("#drawer").hidden ? $("#gq") : $("#dq");
      if (q) { e.preventDefault(); q.focus(); }
    }
  });
  document.addEventListener("keydown", (e) => {
    if ($("#drawer").hidden || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "Escape") closeDetail();
    else if (e.key === "ArrowRight") navDetail(1);
    else if (e.key === "ArrowLeft") navDetail(-1);
    else if (e.key === "c" || e.key === "C") toggleCheck();
  });
  const [tab, tk] = decodeURIComponent(location.hash.slice(1)).split("/");
  setTab(LISTS[tab] || ["similar", "track", "sector", "help"].includes(tab) ? tab : "box");
  if (tab === "similar" && tk) openSim([{ t: tk }], 0);
  if (tk && LISTS[tab]) {
    const rows = filtered(tab);
    let i = rows.findIndex((r) => r.t === tk);
    if (i < 0) { const all = D.lists[tab]; i = all.findIndex((r) => r.t === tk); if (i >= 0) openDetail(tab, all, i); }
    else openDetail(tab, rows, i);
  }
}
init();

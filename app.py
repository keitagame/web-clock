#!/usr/bin/env python3
"""
高精度 Web 時計 - Python バックエンド
様々な方法で現在時刻を取得して返すAPIサーバー
"""

import time
import datetime
import threading
import asyncio
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# NTPライブラリ (optional)
try:
    import ntplib
    NTP_AVAILABLE = True
except ImportError:
    NTP_AVAILABLE = False

app = FastAPI(title="高精度時計 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== NTP キャッシュ (毎回問い合わせると遅いので30秒ごとに更新) =====
_ntp_cache = {"offset": 0.0, "last_updated": 0.0, "server": "pool.ntp.org", "stratum": None, "error": None}
_ntp_lock = threading.Lock()

def _fetch_ntp():
    if not NTP_AVAILABLE:
        return
    try:
        c = ntplib.NTPClient()
        response = c.request(_ntp_cache["server"], version=3, timeout=3)
        with _ntp_lock:
            _ntp_cache["offset"] = response.offset
            _ntp_cache["stratum"] = response.stratum
            _ntp_cache["last_updated"] = time.time()
            _ntp_cache["error"] = None
    except Exception as e:
        with _ntp_lock:
            _ntp_cache["error"] = str(e)

def _ntp_updater():
    while True:
        _fetch_ntp()
        time.sleep(30)

if NTP_AVAILABLE:
    _fetch_ntp()
    t = threading.Thread(target=_ntp_updater, daemon=True)
    t.start()

# ===== アプリ起動時刻 (perf_counter のベースライン) =====
_app_start_perf = time.perf_counter()
_app_start_epoch = time.time()

def get_perf_based_time() -> float:
    """time.perf_counter() を使った高分解能時刻"""
    elapsed = time.perf_counter() - _app_start_perf
    return _app_start_epoch + elapsed


# ===== 時刻取得エンドポイント =====
@app.get("/api/time")
def get_all_times():
    # 1) time.time() — Unix POSIX 秒 (float)
    t_time = time.time()

    # 2) time.time_ns() — ナノ秒整数
    t_ns = time.time_ns()

    # 3) datetime.now() — ローカル時刻
    dt_local = datetime.datetime.now()

    # 4) datetime.now(timezone.utc) — UTC (aware)
    dt_utc = datetime.datetime.now(datetime.timezone.utc)

    # 5) perf_counter ベースの高精度時刻
    t_perf = get_perf_based_time()
    perf_elapsed = time.perf_counter() - _app_start_perf

    # 6) NTP 補正済み時刻
    with _ntp_lock:
        ntp_offset = _ntp_cache["offset"]
        ntp_stratum = _ntp_cache["stratum"]
        ntp_error = _ntp_cache["error"]
        ntp_last_updated = _ntp_cache["last_updated"]
    t_ntp = t_time + ntp_offset if NTP_AVAILABLE else None

    # 7) monotonic (参考: ドリフトなし単調増加)
    t_mono = time.monotonic()

    return {
        "sources": [
            {
                "id": "time_time",
                "label": "time.time()",
                "method": "Python time モジュール (POSIX epoch秒, float)",
                "epoch": t_time,
                "iso": datetime.datetime.fromtimestamp(t_time, tz=datetime.timezone.utc).isoformat(),
                "precision": "microsecond",
                "note": "OSシステムクロック。CPUコストは最も低い。"
            },
            {
                "id": "time_ns",
                "label": "time.time_ns()",
                "method": "Python time モジュール (ナノ秒整数, int)",
                "epoch": t_ns / 1e9,
                "epoch_ns": t_ns,
                "iso": datetime.datetime.fromtimestamp(t_ns / 1e9, tz=datetime.timezone.utc).isoformat(),
                "precision": "nanosecond",
                "note": "Python 3.7+。浮動小数点誤差なしのナノ秒整数。"
            },
            {
                "id": "datetime_local",
                "label": "datetime.now()",
                "method": "datetime.datetime.now() — ローカルタイムゾーン",
                "epoch": dt_local.timestamp(),
                "iso": dt_local.isoformat(),
                "precision": "microsecond",
                "note": f"サーバーのローカル時刻。TZ = {datetime.datetime.now().astimezone().tzname()}"
            },
            {
                "id": "datetime_utc",
                "label": "datetime.now(UTC)",
                "method": "datetime.datetime.now(timezone.utc) — UTC aware",
                "epoch": dt_utc.timestamp(),
                "iso": dt_utc.isoformat(),
                "precision": "microsecond",
                "note": "タイムゾーン情報付き UTC。推奨される現代的な方法。"
            },
            {
                "id": "perf_counter",
                "label": "time.perf_counter()",
                "method": "perf_counter + 起動時エポック補正",
                "epoch": t_perf,
                "iso": datetime.datetime.fromtimestamp(t_perf, tz=datetime.timezone.utc).isoformat(),
                "precision": "sub-microsecond",
                "perf_elapsed_ns": int(perf_elapsed * 1e9),
                "note": "OS最高精度クロック。起動時のエポックに加算して実時刻に変換。"
            },
            {
                "id": "ntp",
                "label": "NTP (pool.ntp.org)",
                "method": "ntplib — ネットワーク時刻プロトコル v3",
                "epoch": t_ntp,
                "iso": datetime.datetime.fromtimestamp(t_ntp, tz=datetime.timezone.utc).isoformat() if t_ntp else None,
                "precision": "millisecond",
                "ntp_offset_ms": round(ntp_offset * 1000, 3) if NTP_AVAILABLE else None,
                "ntp_stratum": ntp_stratum,
                "ntp_last_sync_ago": round(time.time() - ntp_last_updated, 1) if ntp_last_updated else None,
                "error": ntp_error,
                "note": "NTPサーバーとのオフセット補正済み時刻。stratum が小さいほど精度高。"
            },
            {
                "id": "monotonic",
                "label": "time.monotonic()",
                "method": "単調増加クロック (絶対時刻なし)",
                "epoch": _app_start_epoch + (t_mono - time.monotonic() + time.monotonic()),
                "monotonic_s": t_mono,
                "iso": datetime.datetime.fromtimestamp(
                    _app_start_epoch + t_mono - (time.monotonic() - t_mono + t_mono - _app_start_perf + _app_start_perf - t_mono + t_mono),
                    tz=datetime.timezone.utc
                ).isoformat(),
                "precision": "nanosecond",
                "note": "逆行しない単調クロック。NTPのステップ調整の影響を受けない。"
            },
        ],
        "server_overhead_ns": int((time.perf_counter() - _app_start_perf - perf_elapsed) * 1e9),
        "request_processed_at_ns": time.time_ns(),
    }


# ===== フロントエンド配信 =====
HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHRONOS — 高精度時計</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&family=Noto+Sans+JP:wght@300;400&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:        #04080f;
    --bg2:       #080e1a;
    --panel:     #0c1424;
    --border:    #1a2a45;
    --amber:     #ffb300;
    --amber-dim: #7a5500;
    --cyan:      #00e5ff;
    --cyan-dim:  #005e69;
    --green:     #00ff88;
    --green-dim: #004d2a;
    --red:       #ff3860;
    --text:      #c8d8f0;
    --text-dim:  #4a6080;
    --font-mono: 'Share Tech Mono', monospace;
    --font-display: 'Orbitron', sans-serif;
    --font-ui:   'Noto Sans JP', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-ui);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── グリッド背景 ── */
  body::before {
    content: '';
    position: fixed; inset: 0;
    background-image:
      linear-gradient(rgba(0,229,255,.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,229,255,.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  /* ── スキャンライン ── */
  body::after {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      to bottom,
      transparent 0px, transparent 3px,
      rgba(0,0,0,.15) 3px, rgba(0,0,0,.15) 4px
    );
    pointer-events: none;
    z-index: 0;
  }

  /* ── ヘッダー ── */
  header {
    position: relative; z-index: 10;
    padding: 2rem 2.5rem 1rem;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: flex-end; gap: 2rem;
    background: linear-gradient(180deg, rgba(0,229,255,.04) 0%, transparent 100%);
  }

  .logo {
    font-family: var(--font-display);
    font-size: clamp(2rem, 5vw, 3.5rem);
    font-weight: 900;
    letter-spacing: .3em;
    color: var(--cyan);
    text-shadow: 0 0 30px rgba(0,229,255,.5), 0 0 60px rgba(0,229,255,.2);
    line-height: 1;
  }

  .logo span { color: var(--amber); text-shadow: 0 0 20px rgba(255,179,0,.6); }

  .subtitle {
    font-family: var(--font-mono);
    font-size: .75rem;
    color: var(--text-dim);
    letter-spacing: .15em;
    padding-bottom: .3rem;
  }

  .status-bar {
    margin-left: auto;
    display: flex; align-items: center; gap: 1.5rem;
    font-family: var(--font-mono); font-size: .7rem;
    color: var(--text-dim);
  }

  .indicator {
    display: flex; align-items: center; gap: .5rem;
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }
  .dot.ntp { background: var(--amber); box-shadow: 0 0 8px var(--amber); }
  @keyframes pulse {
    0%,100% { opacity: 1; } 50% { opacity: .4; }
  }

  /* ── MASTER CLOCK ── */
  .master {
    position: relative; z-index: 10;
    text-align: center;
    padding: 2.5rem 2rem 2rem;
    border-bottom: 1px solid var(--border);
    background: radial-gradient(ellipse at center top, rgba(0,229,255,.06) 0%, transparent 70%);
  }

  .master-label {
    font-family: var(--font-mono); font-size: .65rem;
    letter-spacing: .3em; color: var(--cyan-dim);
    margin-bottom: .8rem;
  }

  .master-time {
    font-family: var(--font-display);
    font-size: clamp(3rem, 10vw, 7rem);
    font-weight: 700;
    color: #fff;
    text-shadow: 0 0 40px rgba(255,255,255,.3);
    letter-spacing: .05em;
    line-height: 1;
  }

  .master-sub {
    font-family: var(--font-mono);
    font-size: clamp(.9rem, 2vw, 1.4rem);
    color: var(--cyan);
    margin-top: .5rem;
    letter-spacing: .1em;
  }

  .master-date {
    font-family: var(--font-mono); font-size: .8rem;
    color: var(--text-dim); margin-top: .5rem; letter-spacing: .15em;
  }

  /* ── グリッドレイアウト ── */
  .grid {
    position: relative; z-index: 10;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 1px;
    background: var(--border);
    border-top: 1px solid var(--border);
  }

  /* ── 各時計パネル ── */
  .clock-card {
    background: var(--panel);
    padding: 1.5rem;
    transition: background .2s;
    position: relative;
    overflow: hidden;
  }

  .clock-card::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: var(--card-accent, var(--cyan));
    opacity: .6;
  }

  .clock-card:hover { background: var(--bg2); }

  .card-header {
    display: flex; align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 1rem;
  }

  .card-id {
    font-family: var(--font-mono); font-size: .6rem;
    letter-spacing: .2em;
    color: var(--card-accent, var(--cyan));
    opacity: .7;
  }

  .card-method {
    font-size: .7rem; color: var(--text-dim);
    font-family: var(--font-mono);
    text-align: right; max-width: 60%;
    line-height: 1.4;
  }

  .card-title {
    font-family: var(--font-display);
    font-size: 1rem; font-weight: 700;
    color: var(--card-accent, var(--cyan));
    letter-spacing: .08em;
    margin-bottom: .3rem;
  }

  /* ── 時刻表示 ── */
  .time-display {
    font-family: var(--font-mono);
    margin: 1rem 0;
  }

  .time-hms {
    font-size: clamp(1.8rem, 4vw, 2.8rem);
    color: #fff;
    letter-spacing: .06em;
    text-shadow: 0 0 15px rgba(255,255,255,.2);
    line-height: 1.1;
  }

  .time-sub {
    font-size: .85rem;
    color: var(--text);
    margin-top: .3rem;
    letter-spacing: .05em;
  }

  .time-epoch {
    font-size: .7rem;
    color: var(--text-dim);
    margin-top: .5rem;
    letter-spacing: .04em;
    word-break: break-all;
  }

  .time-ns {
    font-size: .75rem;
    margin-top: .25rem;
    letter-spacing: .04em;
  }

  /* ── バッジ ── */
  .badges { display: flex; flex-wrap: wrap; gap: .4rem; margin: .8rem 0; }
  .badge {
    font-family: var(--font-mono); font-size: .6rem;
    padding: .15rem .5rem; border-radius: 2px;
    letter-spacing: .1em; border: 1px solid;
  }
  .badge-precision { color: var(--green); border-color: var(--green-dim); background: rgba(0,255,136,.06); }
  .badge-ntp { color: var(--amber); border-color: var(--amber-dim); background: rgba(255,179,0,.06); }
  .badge-offset {
    color: var(--red);
    border-color: rgba(255,56,96,.4);
    background: rgba(255,56,96,.06);
  }

  /* ── 詳細メタ情報 ── */
  .meta {
    font-size: .68rem; color: var(--text-dim);
    border-top: 1px solid var(--border);
    padding-top: .8rem; margin-top: .8rem;
    line-height: 1.7;
    font-family: var(--font-mono);
  }

  /* ── NTP オフセットグラフ ── */
  .offset-bar-wrap {
    margin: .5rem 0;
    height: 4px; background: var(--border); border-radius: 2px;
    overflow: visible; position: relative;
  }
  .offset-bar {
    height: 100%; border-radius: 2px;
    transition: width .3s, background .3s;
    position: relative;
  }

  /* ── ページボトム ── */
  footer {
    position: relative; z-index: 10;
    text-align: center;
    padding: 1.5rem;
    font-family: var(--font-mono); font-size: .65rem;
    color: var(--text-dim); letter-spacing: .15em;
    border-top: 1px solid var(--border);
  }

  /* ── 更新フラッシュ ── */
  @keyframes flash {
    0% { opacity: 1; } 50% { opacity: .3; } 100% { opacity: 1; }
  }
  .flash { animation: flash .15s ease; }

  /* ── レスポンシブ ── */
  @media (max-width: 600px) {
    header { flex-direction: column; gap: .5rem; }
    .status-bar { margin-left: 0; }
  }
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">CHRON<span>OS</span></div>
    <div class="subtitle">HIGH-PRECISION TIME SOURCE MONITOR / 高精度時刻比較システム</div>
  </div>
  <div class="status-bar">
    <div class="indicator"><div class="dot"></div><span id="freq-label">-- Hz</span></div>
    <div class="indicator"><div class="dot ntp"></div><span id="ntp-status">NTP --</span></div>
    <div>RTT: <span id="rtt-val">--</span> ms</div>
  </div>
</header>

<section class="master">
  <div class="master-label">◈ CLIENT LOCAL TIME — INTERPOLATED ◈</div>
  <div class="master-time" id="m-hms">--:--:--</div>
  <div class="master-sub" id="m-ms">.---</div>
  <div class="master-date" id="m-date">----/--/--</div>
</section>

<div class="grid" id="card-grid"></div>

<footer>CHRONOS v1.0 · Python Backend × Vanilla JS Frontend · 複数時刻源リアルタイム比較</footer>

<script>
// =========== カード定義 ===========
const CARD_DEFS = [
  { id: 'time_time',     accent: '#00e5ff', icon: '①' },
  { id: 'time_ns',       accent: '#00ff88', icon: '②' },
  { id: 'datetime_local',accent: '#ffb300', icon: '③' },
  { id: 'datetime_utc',  accent: '#b39ddb', icon: '④' },
  { id: 'perf_counter',  accent: '#ff6e40', icon: '⑤' },
  { id: 'ntp',           accent: '#ff3860', icon: '⑥' },
];

// =========== 状態 ===========
let lastData = null;
let lastFetchTime = null;  // performance.now() at fetch
let lastEpoch = null;      // epoch from server (NTP adjusted or time.time)
let rttMs = 0;
let fetchCount = 0;
let frameId = null;
const UPDATE_INTERVAL = 100; // ms

// =========== カード生成 ===========
function buildCards() {
  const grid = document.getElementById('card-grid');
  CARD_DEFS.forEach(def => {
    const card = document.createElement('div');
    card.className = 'clock-card';
    card.id = `card-${def.id}`;
    card.style.setProperty('--card-accent', def.accent);
    card.innerHTML = `
      <div class="card-header">
        <div>
          <div class="card-id">${def.icon} SOURCE · ${def.id.toUpperCase()}</div>
          <div class="card-title" id="title-${def.id}">--</div>
        </div>
        <div class="card-method" id="method-${def.id}">--</div>
      </div>
      <div class="time-display">
        <div class="time-hms" id="hms-${def.id}">--:--:--</div>
        <div class="time-sub" id="sub-${def.id}">--</div>
        <div class="time-ns"  id="ns-${def.id}" style="color:${def.accent}88"></div>
        <div class="time-epoch" id="epoch-${def.id}">epoch: --</div>
      </div>
      <div class="badges" id="badges-${def.id}"></div>
      <div class="meta" id="meta-${def.id}">--</div>
    `;
    grid.appendChild(card);
  });
}

// =========== 時刻フォーマット ===========
function epochToLocal(epoch) {
  return new Date(epoch * 1000);
}

function pad(n, w=2) { return String(Math.floor(n)).padStart(w, '0'); }

function formatHMS(d) {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatMs(d) {
  return `.${pad(d.getMilliseconds(), 3)}`;
}

function formatMicro(epoch) {
  // epoch: float秒 → マイクロ秒部分
  const us = Math.floor((epoch % 1) * 1e6);
  return `.${pad(Math.floor(us/1000),3)} ${pad(us%1000,3)} μs`;
}

function formatNano(epoch_ns) {
  if (epoch_ns == null) return '';
  const ns = BigInt(epoch_ns);
  const ms = ns / 1000000n;
  const rem_us = (ns % 1000000n) / 1000n;
  const rem_ns = ns % 1000n;
  return `${ms} ms | ${rem_us} μs | ${rem_ns} ns (raw)`;
}

// =========== カード更新 ===========
function updateCard(src, interpEpoch) {
  if (!src) return;
  const id = src.id;

  // タイトル/メソッド (初回のみ)
  document.getElementById(`title-${id}`).textContent = src.label || id;
  document.getElementById(`method-${id}`).textContent = src.method || '';

  // epoch決定: 補間するか？
  let epoch = interpEpoch != null ? interpEpoch : src.epoch;
  if (epoch == null) {
    // NTP失敗など
    document.getElementById(`hms-${id}`).textContent = 'N/A';
    document.getElementById(`sub-${id}`).textContent = src.error || '取得失敗';
    return;
  }

  const d = epochToLocal(epoch);
  document.getElementById(`hms-${id}`).textContent = formatHMS(d);

  // サブ表示: ns ソースはナノ秒まで, それ以外はマイクロ秒
  if (id === 'time_ns' && src.epoch_ns != null) {
    document.getElementById(`sub-${id}`).textContent = formatMicro(epoch);
    document.getElementById(`ns-${id}`).textContent = formatNano(src.epoch_ns);
  } else if (id === 'perf_counter' && src.perf_elapsed_ns != null) {
    document.getElementById(`sub-${id}`).textContent = formatMicro(epoch);
    const ns = src.perf_elapsed_ns;
    document.getElementById(`ns-${id}`).textContent =
      `経過 ${(ns/1e9).toFixed(6)} s (perf_counter)`;
  } else {
    document.getElementById(`sub-${id}`).textContent = formatMicro(epoch);
    document.getElementById(`ns-${id}`).textContent = '';
  }

  document.getElementById(`epoch-${id}`).textContent =
    `epoch: ${epoch.toFixed(9)}`;

  // バッジ
  let badges = `<span class="badge badge-precision">${src.precision || '?'}</span>`;
  if (id === 'ntp' && src.ntp_offset_ms != null) {
    const sign = src.ntp_offset_ms >= 0 ? '+' : '';
    badges += `<span class="badge badge-ntp">STR.${src.ntp_stratum ?? '?'}</span>`;
    badges += `<span class="badge badge-offset">offset ${sign}${src.ntp_offset_ms} ms</span>`;
    if (src.ntp_last_sync_ago != null)
      badges += `<span class="badge badge-precision">sync ${src.ntp_last_sync_ago}s前</span>`;
  }
  document.getElementById(`badges-${id}`).innerHTML = badges;

  // メタ
  document.getElementById(`meta-${id}`).textContent = src.note || '';
}

// =========== マスタークロック (raf補間) ===========
function animateMaster() {
  if (lastEpoch != null && lastFetchTime != null) {
    const elapsed = (performance.now() - lastFetchTime) / 1000;
    const now = lastEpoch + elapsed;
    const d = epochToLocal(now);
    document.getElementById('m-hms').textContent = formatHMS(d);
    document.getElementById('m-ms').textContent = formatMs(d);
    const yr = d.getFullYear(), mo = pad(d.getMonth()+1), dy = pad(d.getDate());
    document.getElementById('m-date').textContent =
      `${yr}/${mo}/${dy} (${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getDay()]}) UTC${new Date().toTimeString().slice(9,15)}`;

    // 各カードも補間更新 (NTP以外はfetch時の差分で補間)
    if (lastData) {
      lastData.sources.forEach(src => {
        if (src.epoch == null) { updateCard(src, null); return; }
        const interpEpoch = src.epoch + elapsed;
        updateCard(src, interpEpoch);
      });
    }
  }
  frameId = requestAnimationFrame(animateMaster);
}

// =========== API フェッチ ===========
async function fetchTime() {
  const t0 = performance.now();
  try {
    const res = await fetch('/api/time');
    const data = await res.json();
    const t1 = performance.now();
    rttMs = Math.round(t1 - t0);

    // サーバー応答の中間時刻 = epoch から RTT/2 遅延ずみ
    // NTPあればNTP優先、なければtime.time
    const ntpSrc = data.sources.find(s => s.id === 'ntp');
    const baseEpoch = (ntpSrc && ntpSrc.epoch != null)
      ? ntpSrc.epoch
      : data.sources.find(s => s.id === 'time_time')?.epoch;

    lastEpoch = baseEpoch + (rttMs / 2) / 1000;
    lastFetchTime = t1;
    lastData = data;
    fetchCount++;

    // ステータス更新
    document.getElementById('freq-label').textContent =
      `${Math.round(1000/UPDATE_INTERVAL)} Hz`;
    document.getElementById('rtt-val').textContent = rttMs;

    if (ntpSrc) {
      const ntpEl = document.getElementById('ntp-status');
      if (ntpSrc.error) {
        ntpEl.textContent = `NTP ERR`;
      } else {
        ntpEl.textContent = `NTP ±${Math.abs(ntpSrc.ntp_offset_ms).toFixed(1)}ms`;
      }
    }

  } catch(e) {
    console.error('fetch error', e);
  }
}

// =========== 初期化 ===========
buildCards();
animateMaster();
fetchTime();
setInterval(fetchTime, UPDATE_INTERVAL);
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    print("=" * 55)
    print(" CHRONOS — 高精度時計サーバー起動")
    print(f" NTP: {'有効 (ntplib)' if NTP_AVAILABLE else '無効 (pip install ntplib)'}")
    print(" http://localhost:8000 でアクセス")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

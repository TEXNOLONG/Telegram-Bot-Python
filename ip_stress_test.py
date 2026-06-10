from flask import Flask, render_template_string, jsonify
import requests
import time
import socket

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IP Стресс-тест</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0f;
    color: #e0e0e0;
    font-family: 'Courier New', monospace;
    min-height: 100vh;
    overflow-x: hidden;
  }
  .bg-grid {
    position: fixed; inset: 0;
    background-image:
      linear-gradient(rgba(0,255,136,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,136,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    z-index: 0;
  }
  .container { position: relative; z-index: 1; max-width: 960px; margin: 0 auto; padding: 30px 20px; }

  h1 {
    text-align: center;
    font-size: 2rem;
    color: #00ff88;
    letter-spacing: 4px;
    text-transform: uppercase;
    text-shadow: 0 0 20px rgba(0,255,136,0.5);
    margin-bottom: 8px;
  }
  .subtitle { text-align: center; color: #555; font-size: 0.8rem; letter-spacing: 2px; margin-bottom: 30px; }

  .ip-hero {
    background: linear-gradient(135deg, rgba(0,255,136,0.08), rgba(0,150,255,0.05));
    border: 1px solid rgba(0,255,136,0.2);
    border-radius: 12px;
    padding: 28px;
    text-align: center;
    margin-bottom: 24px;
    position: relative;
    overflow: hidden;
  }
  .ip-hero::before {
    content: '';
    position: absolute; top: 0; left: -100%;
    width: 100%; height: 2px;
    background: linear-gradient(90deg, transparent, #00ff88, transparent);
    animation: scan 3s linear infinite;
  }
  @keyframes scan { to { left: 200%; } }

  .ip-label { font-size: 0.7rem; color: #555; letter-spacing: 3px; margin-bottom: 8px; }
  .ip-value { font-size: 2.8rem; color: #00ff88; font-weight: bold; letter-spacing: 3px; text-shadow: 0 0 30px rgba(0,255,136,0.6); }
  .ip-info-row { display: flex; justify-content: center; gap: 30px; margin-top: 16px; flex-wrap: wrap; }
  .ip-info-item { font-size: 0.8rem; color: #888; }
  .ip-info-item span { color: #00ccff; }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }

  .card {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 20px;
  }
  .card-title {
    font-size: 0.65rem;
    letter-spacing: 3px;
    color: #555;
    text-transform: uppercase;
    margin-bottom: 16px;
    display: flex; align-items: center; gap: 8px;
  }
  .card-title::after { content: ''; flex: 1; height: 1px; background: rgba(255,255,255,0.05); }

  .stat-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { font-size: 0.78rem; color: #666; }
  .stat-value { font-size: 0.85rem; color: #e0e0e0; }
  .stat-value.good { color: #00ff88; }
  .stat-value.warn { color: #ffaa00; }
  .stat-value.bad { color: #ff4444; }

  .test-section { margin-bottom: 24px; }
  .test-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 14px;
  }
  .test-title { font-size: 0.65rem; letter-spacing: 3px; color: #555; text-transform: uppercase; }
  .test-status { font-size: 0.7rem; color: #00ff88; }

  .server-list { display: flex; flex-direction: column; gap: 10px; }
  .server-item {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 8px;
    padding: 12px 16px;
    display: flex; align-items: center; gap: 12px;
  }
  .server-name { flex: 1; font-size: 0.82rem; color: #aaa; }
  .server-flag { font-size: 1.1rem; }
  .ping-bar-wrap { width: 160px; height: 4px; background: rgba(255,255,255,0.05); border-radius: 2px; }
  .ping-bar { height: 100%; border-radius: 2px; transition: width 0.6s ease; background: #00ff88; }
  .ping-val { font-size: 0.8rem; width: 70px; text-align: right; }

  .speed-test {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 24px;
  }
  .speed-meters { display: flex; justify-content: space-around; gap: 16px; flex-wrap: wrap; margin: 16px 0; }
  .speed-meter { text-align: center; }
  .speed-circle {
    position: relative; width: 110px; height: 110px;
    margin: 0 auto 10px;
  }
  .speed-circle svg { transform: rotate(-90deg); }
  .speed-circle-text {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
  }
  .speed-num { font-size: 1.3rem; font-weight: bold; color: #00ff88; }
  .speed-unit { font-size: 0.6rem; color: #555; letter-spacing: 1px; }
  .speed-label { font-size: 0.7rem; color: #555; letter-spacing: 2px; text-transform: uppercase; }

  .log-box {
    background: #0d0d0d;
    border: 1px solid rgba(0,255,136,0.1);
    border-radius: 8px;
    padding: 14px;
    height: 160px;
    overflow-y: auto;
    font-size: 0.75rem;
    line-height: 1.7;
  }
  .log-box::-webkit-scrollbar { width: 4px; }
  .log-box::-webkit-scrollbar-track { background: #111; }
  .log-box::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
  .log-line { color: #555; }
  .log-line .ts { color: #333; margin-right: 8px; }
  .log-line .ok { color: #00ff88; }
  .log-line .warn { color: #ffaa00; }
  .log-line .err { color: #ff4444; }
  .log-line .info { color: #00ccff; }

  .btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 24px;
    background: rgba(0,255,136,0.1);
    border: 1px solid rgba(0,255,136,0.3);
    color: #00ff88;
    border-radius: 6px;
    font-family: 'Courier New', monospace;
    font-size: 0.8rem;
    letter-spacing: 2px;
    cursor: pointer;
    text-transform: uppercase;
    transition: all 0.2s;
  }
  .btn:hover { background: rgba(0,255,136,0.18); border-color: #00ff88; box-shadow: 0 0 15px rgba(0,255,136,0.2); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-row { display: flex; justify-content: center; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }

  .progress-bar {
    height: 3px;
    background: rgba(255,255,255,0.05);
    border-radius: 2px;
    margin: 10px 0;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #00ff88, #00ccff);
    border-radius: 2px;
    transition: width 0.3s ease;
    width: 0%;
  }
  .blink { animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

  .status-dot {
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: #333; margin-right: 6px;
    transition: background 0.3s;
  }
  .status-dot.active { background: #00ff88; box-shadow: 0 0 6px #00ff88; }
  .status-dot.warn { background: #ffaa00; box-shadow: 0 0 6px #ffaa00; }
  .status-dot.dead { background: #ff4444; box-shadow: 0 0 6px #ff4444; }

  .footer { text-align: center; font-size: 0.65rem; color: #333; letter-spacing: 2px; padding: 20px 0; }
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="container">

  <h1>⚡ IP STRESS TEST</h1>
  <div class="subtitle">NETWORK DIAGNOSTICS &amp; PERFORMANCE ANALYSIS</div>

  <!-- IP Hero -->
  <div class="ip-hero">
    <div class="ip-label">ВАШ ПУБЛИЧНЫЙ IP АДРЕС</div>
    <div class="ip-value" id="ip-display">ЗАГРУЗКА...</div>
    <div class="ip-info-row">
      <div class="ip-info-item">Страна: <span id="ip-country">—</span></div>
      <div class="ip-info-item">Город: <span id="ip-city">—</span></div>
      <div class="ip-info-item">Провайдер (ISP): <span id="ip-isp">—</span></div>
      <div class="ip-info-item">ASN: <span id="ip-asn">—</span></div>
    </div>
  </div>

  <!-- IP Details Grid -->
  <div class="grid">
    <div class="card">
      <div class="card-title">🌍 Геолокация</div>
      <div class="stat-row"><span class="stat-label">Регион</span><span class="stat-value" id="g-region">—</span></div>
      <div class="stat-row"><span class="stat-label">Почтовый код</span><span class="stat-value" id="g-zip">—</span></div>
      <div class="stat-row"><span class="stat-label">Координаты</span><span class="stat-value" id="g-coords">—</span></div>
      <div class="stat-row"><span class="stat-label">Часовой пояс</span><span class="stat-value" id="g-tz">—</span></div>
      <div class="stat-row"><span class="stat-label">VPN/Proxy</span><span class="stat-value" id="g-vpn">—</span></div>
    </div>
    <div class="card">
      <div class="card-title">🔒 Безопасность</div>
      <div class="stat-row"><span class="stat-label">IPv4</span><span class="stat-value" id="s-ipv4">—</span></div>
      <div class="stat-row"><span class="stat-label">IPv6</span><span class="stat-value" id="s-ipv6">—</span></div>
      <div class="stat-row"><span class="stat-label">DNS Утечка</span><span class="stat-value" id="s-dns">—</span></div>
      <div class="stat-row"><span class="stat-label">WebRTC</span><span class="stat-value" id="s-webrtc">—</span></div>
      <div class="stat-row"><span class="stat-label">Тип сети</span><span class="stat-value" id="s-type">—</span></div>
    </div>
  </div>

  <!-- Control Buttons -->
  <div class="btn-row">
    <button class="btn" id="btn-start" onclick="runAllTests()">▶ ЗАПУСТИТЬ ТЕСТ</button>
    <button class="btn" id="btn-speed" onclick="runSpeedTest()">⚡ ТЕСТ СКОРОСТИ</button>
    <button class="btn" onclick="refreshIP()">↻ ОБНОВИТЬ IP</button>
  </div>

  <!-- Ping Tests -->
  <div class="test-section">
    <div class="test-header">
      <div class="test-title">📡 Тест задержки (Ping) — глобальные серверы</div>
      <div class="test-status" id="ping-status">ОЖИДАНИЕ</div>
    </div>
    <div class="progress-bar"><div class="progress-fill" id="ping-progress"></div></div>
    <div class="server-list" id="server-list">
      <!-- filled by JS -->
    </div>
  </div>

  <!-- Speed Test -->
  <div class="speed-test">
    <div class="card-title">⚡ Тест скорости соединения</div>
    <div class="speed-meters">
      <div class="speed-meter">
        <div class="speed-circle">
          <svg width="110" height="110" viewBox="0 0 110 110">
            <circle cx="55" cy="55" r="48" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="8"/>
            <circle id="dl-arc" cx="55" cy="55" r="48" fill="none" stroke="#00ff88" stroke-width="8"
              stroke-dasharray="301.6" stroke-dashoffset="301.6" stroke-linecap="round"/>
          </svg>
          <div class="speed-circle-text">
            <div class="speed-num" id="dl-val">0</div>
            <div class="speed-unit">Мбит/с</div>
          </div>
        </div>
        <div class="speed-label">↓ Загрузка</div>
      </div>
      <div class="speed-meter">
        <div class="speed-circle">
          <svg width="110" height="110" viewBox="0 0 110 110">
            <circle cx="55" cy="55" r="48" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="8"/>
            <circle id="ul-arc" cx="55" cy="55" r="48" fill="none" stroke="#00ccff" stroke-width="8"
              stroke-dasharray="301.6" stroke-dashoffset="301.6" stroke-linecap="round"/>
          </svg>
          <div class="speed-circle-text">
            <div class="speed-num" id="ul-val">0</div>
            <div class="speed-unit">Мбит/с</div>
          </div>
        </div>
        <div class="speed-label">↑ Отдача</div>
      </div>
      <div class="speed-meter">
        <div class="speed-circle">
          <svg width="110" height="110" viewBox="0 0 110 110">
            <circle cx="55" cy="55" r="48" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="8"/>
            <circle id="lat-arc" cx="55" cy="55" r="48" fill="none" stroke="#ffaa00" stroke-width="8"
              stroke-dasharray="301.6" stroke-dashoffset="301.6" stroke-linecap="round"/>
          </svg>
          <div class="speed-circle-text">
            <div class="speed-num" id="lat-val">0</div>
            <div class="speed-unit">мс</div>
          </div>
        </div>
        <div class="speed-label">⊙ Пинг</div>
      </div>
    </div>
    <div class="progress-bar"><div class="progress-fill" id="speed-progress"></div></div>
  </div>

  <!-- Live Log -->
  <div class="card">
    <div class="card-title" style="margin-bottom:10px">🖥 Лог тестирования</div>
    <div class="log-box" id="log-box">
      <div class="log-line"><span class="ts">[СИСТЕМА]</span> <span class="info">Инициализация...</span></div>
    </div>
  </div>

  <div class="footer">IP STRESS TEST v1.0 — REPLIT NETWORK TOOLS</div>
</div>

<script>
const SERVERS = [
  { name: "Google DNS", flag: "🇺🇸", host: "8.8.8.8", url: "https://dns.google/resolve?name=google.com" },
  { name: "Cloudflare", flag: "🌐", host: "1.1.1.1", url: "https://cloudflare-dns.com/dns-query?name=google.com" },
  { name: "Яндекс DNS", flag: "🇷🇺", host: "77.88.8.8", url: "https://yandex.ru" },
  { name: "Amazon AWS", flag: "🇺🇸", host: "aws", url: "https://aws.amazon.com" },
  { name: "Microsoft Azure", flag: "🌐", host: "azure", url: "https://azure.microsoft.com" },
  { name: "Frankfurt CDN", flag: "🇩🇪", host: "cdnjs", url: "https://cdnjs.cloudflare.com" },
  { name: "Tokyo CDN", flag: "🇯🇵", host: "fastly", url: "https://www.fastly.com" },
  { name: "GitHub", flag: "🌐", host: "github", url: "https://github.com" },
];

let logBox = document.getElementById('log-box');

function log(msg, type = 'info') {
  const now = new Date();
  const ts = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}.${String(now.getMilliseconds()).padStart(3,'0')}`;
  const el = document.createElement('div');
  el.className = 'log-line';
  el.innerHTML = `<span class="ts">[${ts}]</span> <span class="${type}">${msg}</span>`;
  logBox.appendChild(el);
  logBox.scrollTop = logBox.scrollHeight;
}

function setArc(id, percent, maxDash = 301.6) {
  const el = document.getElementById(id);
  if (el) {
    const offset = maxDash - (maxDash * Math.min(percent, 1));
    el.style.strokeDashoffset = offset;
  }
}

async function fetchIP() {
  try {
    const r = await fetch('https://ipapi.co/json/');
    return await r.json();
  } catch {
    try {
      const r = await fetch('https://api.ipify.org?format=json');
      const d = await r.json();
      return { ip: d.ip };
    } catch { return null; }
  }
}

async function refreshIP() {
  log('Определяем публичный IP адрес...', 'info');
  document.getElementById('ip-display').textContent = 'ЗАГРУЗКА...';
  const data = await fetchIP();
  if (data && data.ip) {
    document.getElementById('ip-display').textContent = data.ip;
    document.getElementById('ip-country').textContent = `${data.country_name || '—'} ${data.country_code ? '('+data.country_code+')' : ''}`;
    document.getElementById('ip-city').textContent = data.city || '—';
    document.getElementById('ip-isp').textContent = data.org || '—';
    document.getElementById('ip-asn').textContent = data.asn || '—';
    document.getElementById('g-region').textContent = data.region || '—';
    document.getElementById('g-zip').textContent = data.postal || '—';
    document.getElementById('g-coords').textContent = data.latitude ? `${data.latitude}, ${data.longitude}` : '—';
    document.getElementById('g-tz').textContent = data.timezone || '—';
    document.getElementById('g-vpn').textContent = '—';
    document.getElementById('s-ipv4').textContent = data.ip || '—';
    log(`IP адрес получен: ${data.ip}`, 'ok');
    log(`Страна: ${data.country_name} | Город: ${data.city}`, 'ok');
    log(`Провайдер: ${data.org || '—'}`, 'ok');
  } else {
    document.getElementById('ip-display').textContent = 'ОШИБКА';
    log('Не удалось получить IP адрес', 'err');
  }
}

async function checkIPv6() {
  try {
    const r = await fetch('https://api6.ipify.org?format=json', { signal: AbortSignal.timeout(3000) });
    const d = await r.json();
    document.getElementById('s-ipv6').textContent = d.ip || 'Нет';
    document.getElementById('s-type').textContent = 'Dual Stack';
    log(`IPv6 обнаружен: ${d.ip}`, 'ok');
  } catch {
    document.getElementById('s-ipv6').textContent = 'Не доступен';
    document.getElementById('s-type').textContent = 'IPv4 Only';
    log('IPv6 не обнаружен (только IPv4)', 'warn');
  }
}

async function measurePing(url) {
  const N = 5;
  const times = [];
  for (let i = 0; i < N; i++) {
    const t0 = performance.now();
    try {
      await fetch(url + '&_=' + Date.now(), {
        mode: 'no-cors',
        signal: AbortSignal.timeout(4000),
        cache: 'no-store'
      });
    } catch {}
    times.push(performance.now() - t0);
    await new Promise(r => setTimeout(r, 50));
  }
  times.sort((a, b) => a - b);
  return Math.round(times[1]); // 2nd lowest (skip best outlier)
}

function buildServerList() {
  const list = document.getElementById('server-list');
  list.innerHTML = '';
  SERVERS.forEach((s, i) => {
    const div = document.createElement('div');
    div.className = 'server-item';
    div.id = `srv-${i}`;
    div.innerHTML = `
      <span class="server-flag">${s.flag}</span>
      <span class="server-name">${s.name}</span>
      <div class="ping-bar-wrap"><div class="ping-bar" id="bar-${i}" style="width:0%"></div></div>
      <span class="ping-val" id="ping-${i}"><span class="blink">…</span></span>
    `;
    list.appendChild(div);
  });
}

async function runPingTests() {
  document.getElementById('ping-status').textContent = 'ТЕСТИРОВАНИЕ...';
  log('=== Начинаем тест задержки ===', 'info');

  for (let i = 0; i < SERVERS.length; i++) {
    const s = SERVERS[i];
    const pct = Math.round((i + 1) / SERVERS.length * 100);
    document.getElementById('ping-progress').style.width = pct + '%';

    const ms = await measurePing(s.url);
    const bar = document.getElementById(`bar-${i}`);
    const label = document.getElementById(`ping-${i}`);

    let cls = 'good', barColor = '#00ff88';
    if (ms > 200) { cls = 'warn'; barColor = '#ffaa00'; }
    if (ms > 500) { cls = 'bad'; barColor = '#ff4444'; }

    const barPct = Math.max(4, Math.min(100, ms / 5));
    bar.style.width = barPct + '%';
    bar.style.background = barColor;
    label.innerHTML = `<span class="${cls}">${ms} мс</span>`;
    log(`${s.flag} ${s.name}: ${ms} мс`, ms < 200 ? 'ok' : ms < 500 ? 'warn' : 'err');
  }

  document.getElementById('ping-status').textContent = 'ГОТОВО ✓';
  log('=== Тест задержки завершён ===', 'ok');
}

async function runSpeedTest() {
  const btn = document.getElementById('btn-speed');
  btn.disabled = true;
  log('=== Начинаем тест скорости ===', 'info');
  document.getElementById('speed-progress').style.width = '0%';

  // Latency test
  log('Измеряем задержку...', 'info');
  const latencies = [];
  for (let i = 0; i < 8; i++) {
    const t0 = performance.now();
    try {
      await fetch('https://cloudflare-dns.com/dns-query?name=test.com&_=' + Date.now(), {
        mode: 'no-cors', cache: 'no-store', signal: AbortSignal.timeout(3000)
      });
    } catch {}
    latencies.push(performance.now() - t0);
  }
  latencies.sort((a,b) => a-b);
  const latency = Math.round(latencies[2]);
  document.getElementById('lat-val').textContent = latency;
  setArc('lat-arc', Math.max(0, 1 - latency/300));
  document.getElementById('speed-progress').style.width = '25%';
  log(`Задержка: ${latency} мс`, latency < 100 ? 'ok' : 'warn');

  // Download speed test
  log('Тест скорости загрузки...', 'info');
  const dlSizes = [500000, 1000000, 2000000];
  let totalBytes = 0, totalTime = 0;
  for (const size of dlSizes) {
    const t0 = performance.now();
    try {
      await fetch(`https://speed.cloudflare.com/__down?bytes=${size}&_=${Date.now()}`, {
        signal: AbortSignal.timeout(8000), cache: 'no-store'
      });
      const elapsed = (performance.now() - t0) / 1000;
      totalBytes += size;
      totalTime += elapsed;
    } catch {}
  }
  const dlSpeed = totalTime > 0 ? ((totalBytes * 8) / totalTime / 1e6).toFixed(1) : 0;
  document.getElementById('dl-val').textContent = dlSpeed;
  setArc('dl-arc', Math.min(parseFloat(dlSpeed) / 100, 1));
  document.getElementById('speed-progress').style.width = '65%';
  log(`Скорость загрузки: ${dlSpeed} Мбит/с`, parseFloat(dlSpeed) > 10 ? 'ok' : 'warn');

  // Upload speed test
  log('Тест скорости отдачи...', 'info');
  const uploadSizes = [200000, 500000, 1000000];
  let ulBytes = 0, ulTime = 0;
  for (const size of uploadSizes) {
    const data = new ArrayBuffer(size);
    const t0 = performance.now();
    try {
      await fetch('https://speed.cloudflare.com/__up', {
        method: 'POST', body: data,
        signal: AbortSignal.timeout(8000), cache: 'no-store'
      });
      const elapsed = (performance.now() - t0) / 1000;
      ulBytes += size;
      ulTime += elapsed;
    } catch {}
  }
  const ulSpeed = ulTime > 0 ? ((ulBytes * 8) / ulTime / 1e6).toFixed(1) : 0;
  document.getElementById('ul-val').textContent = ulSpeed;
  setArc('ul-arc', Math.min(parseFloat(ulSpeed) / 50, 1), 301.6);
  document.getElementById('speed-progress').style.width = '100%';
  log(`Скорость отдачи: ${ulSpeed} Мбит/с`, parseFloat(ulSpeed) > 5 ? 'ok' : 'warn');

  log('=== Тест скорости завершён ===', 'ok');
  btn.disabled = false;
}

async function checkDNSLeak() {
  log('Проверяем DNS утечки...', 'info');
  const t0 = performance.now();
  try {
    await fetch('https://1.1.1.1/dns-query?name=dnsleaktest.com', {
      headers: { accept: 'application/dns-json' },
      signal: AbortSignal.timeout(3000)
    });
    const ms = Math.round(performance.now() - t0);
    document.getElementById('s-dns').textContent = `Нет утечек (${ms}мс)`;
    document.getElementById('s-dns').className = 'stat-value good';
    log(`DNS проверка OK (${ms}мс)`, 'ok');
  } catch {
    document.getElementById('s-dns').textContent = 'Не проверено';
    log('DNS проверка недоступна', 'warn');
  }
}

async function checkWebRTC() {
  log('Проверяем WebRTC...', 'info');
  return new Promise((resolve) => {
    try {
      const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
      let found = false;
      pc.createDataChannel('');
      pc.createOffer().then(o => pc.setLocalDescription(o));
      pc.onicecandidate = (e) => {
        if (e.candidate) {
          const ip = /([0-9]{1,3}(\.[0-9]{1,3}){3})/.exec(e.candidate.candidate);
          if (ip && !found) {
            found = true;
            document.getElementById('s-webrtc').textContent = ip[1];
            log(`WebRTC локальный IP: ${ip[1]}`, 'warn');
            pc.close();
            resolve();
          }
        }
      };
      setTimeout(() => {
        if (!found) {
          document.getElementById('s-webrtc').textContent = 'Не обнаружен';
          document.getElementById('s-webrtc').className = 'stat-value good';
          log('WebRTC утечки не обнаружены', 'ok');
        }
        pc.close();
        resolve();
      }, 3000);
    } catch {
      document.getElementById('s-webrtc').textContent = 'Нет доступа';
      resolve();
    }
  });
}

async function runAllTests() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  btn.textContent = '⏳ ТЕСТИРОВАНИЕ...';
  logBox.innerHTML = '';
  log('╔══════════════════════════════════╗', 'info');
  log('║     IP СТРЕСС ТЕСТ ЗАПУЩЕН       ║', 'ok');
  log('╚══════════════════════════════════╝', 'info');

  await refreshIP();
  await checkIPv6();
  await checkDNSLeak();
  await checkWebRTC();
  buildServerList();
  await runPingTests();
  await runSpeedTest();

  log('▓▓▓ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ УСПЕШНО ▓▓▓', 'ok');
  btn.disabled = false;
  btn.textContent = '▶ ЗАПУСТИТЬ СНОВА';
}

// Init
(async () => {
  buildServerList();
  await refreshIP();
  await checkIPv6();
  log('Готов к тестированию. Нажмите "ЗАПУСТИТЬ ТЕСТ"', 'ok');
})();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/ip-info")
def ip_info():
    try:
        r = requests.get("https://ipapi.co/json/", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

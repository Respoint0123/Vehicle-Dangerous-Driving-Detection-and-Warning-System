// static/app.js

const POLL_STATUS_MS = 500;
const POLL_ALERTS_MS = 5000;

// 元素引用
const elSpeed  = document.getElementById("stat-speed");
const elAngle  = document.getElementById("stat-angle");
const elLanes  = document.getElementById("stat-lanes");
const elBanner = document.getElementById("alert-banner");
const elTbody  = document.getElementById("alert-tbody");
const cfgForm  = document.getElementById("config-form");
const srcForm  = document.getElementById("source-form");

// 状态轮询
let _bannerTimer = null;

async function pollStatus() {
  try {
    const res  = await fetch("/api/status");
    const data = await res.json();

    elSpeed.textContent = data.speed_kmh.toFixed(1);
    elAngle.textContent = Math.abs(data.turn_angle).toFixed(1);

    if (data.lanes) {
      elLanes.textContent = "已检测";
      elLanes.className   = "stat-badge";
    } else {
      elLanes.textContent = "未检测";
      elLanes.className   = "stat-badge off";
    }

    if (data.alert && data.alert.length > 0) {
      showBanner(data.alert);
    }
  } catch (_) {
    // 网络断开时静默忽略
  }
}

function showBanner(alerts) {
  const labels = { speed: "超速预警", turn: "急转弯预警" };
  elBanner.textContent = alerts.map(a => labels[a] || a.toUpperCase()).join(" | ");
  elBanner.style.display = "block";

  clearTimeout(_bannerTimer);
  _bannerTimer = setTimeout(() => {
    elBanner.style.display = "none";
  }, 2000);
}

// 历史记录轮询
async function pollAlerts() {
  try {
    const res  = await fetch("/api/alerts?limit=30");
    const rows = await res.json();
    renderAlerts(rows);
  } catch (_) {
    // 静默忽略
  }
}

function renderAlerts(rows) {
  if (!rows.length) {
    elTbody.innerHTML =
      '<tr><td colspan="4" style="color:var(--muted);padding:12px 0;">暂无记录</td></tr>';
    return;
  }
  elTbody.innerHTML = rows.map(r => {
    const tagClass = r.alert_type === "speed" ? "speed" : "turn";
    const tagLabel = r.alert_type === "speed" ? "超速" : "急转";
    const time     = r.timestamp.replace("T", " ");
    return `<tr>
      <td>${time}</td>
      <td><span class="tag ${tagClass}">${tagLabel}</span></td>
      <td>${r.speed_kmh.toFixed(1)}</td>
      <td>${r.angle_deg.toFixed(1)}</td>
    </tr>`;
  }).join("");
}

// 阈值配置
cfgForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const speedVal    = parseFloat(document.getElementById("cfg-speed").value);
  const angleVal    = parseFloat(document.getElementById("cfg-angle").value);
  const cooldownVal = parseFloat(document.getElementById("cfg-cooldown").value);
  if (isNaN(speedVal) || isNaN(angleVal) || isNaN(cooldownVal)) return;
  const body = {
    speed_limit: speedVal,
    angle_limit: angleVal,
    cooldown:    cooldownVal,
  };
  try {
    const res  = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    document.getElementById("cfg-speed").value    = data.speed_limit;
    document.getElementById("cfg-angle").value    = data.angle_limit;
    document.getElementById("cfg-cooldown").value = data.cooldown;
  } catch (_) {}
});

// 视频源切换
srcForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const source = document.getElementById("src-input").value.trim();
  if (!source) return;
  try {
    await fetch("/api/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    // 强制刷新 img src，绕过浏览器缓存
    const feed = document.getElementById("video-feed");
    feed.src = "/video_feed?" + Date.now();
  } catch (_) {}
});

// 启动
pollStatus();
pollAlerts();
setInterval(pollStatus, POLL_STATUS_MS);
setInterval(pollAlerts, POLL_ALERTS_MS);

// 透视标定
const elCalibCanvas   = document.getElementById("calib-canvas");
const elCalibControls = document.getElementById("calib-controls");
const elCalibPreview  = document.getElementById("calib-preview");
const btnCalibrate    = document.getElementById("btn-calibrate");
const btnSaveCalib    = document.getElementById("btn-save-calib");
const btnCancelCalib  = document.getElementById("btn-cancel-calib");
const elVideoFeed     = document.getElementById("video-feed");

let _calibActive  = false;
let _calibPoints  = [];
let _calibBackup  = [];
let _dragging     = -1;
let _previewTimer = null;

const POINT_COLORS = ["#ff4560", "#29d982", "#ffb020", "#cc55ff"];
const POINT_LABELS = ["左下", "左上", "右上", "右下"];
const HIT_RADIUS   = 14;
const DRAW_RADIUS  = 8;

function getImageRenderRect() {
  const imgRect    = elVideoFeed.getBoundingClientRect();
  const canvasRect = elCalibCanvas.getBoundingClientRect();
  return {
    x: imgRect.left - canvasRect.left,
    y: imgRect.top  - canvasRect.top,
    w: imgRect.width,
    h: imgRect.height,
  };
}

function ratioToScreen(rx, ry, rect) {
  return { x: rect.x + rx * rect.w, y: rect.y + ry * rect.h };
}

function screenToRatio(sx, sy, rect) {
  return {
    rx: Math.max(0, Math.min(1, (sx - rect.x) / rect.w)),
    ry: Math.max(0, Math.min(1, (sy - rect.y) / rect.h)),
  };
}

function drawCalibCanvas() {
  const canvas = elCalibCanvas;
  const ctx    = canvas.getContext("2d");
  canvas.width  = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!_calibActive || _calibPoints.length < 4) return;
  const rect = getImageRenderRect();

  const order = [0, 1, 2, 3, 0];
  ctx.beginPath();
  for (let i = 0; i < order.length; i++) {
    const [rx, ry] = _calibPoints[order[i]];
    const s = ratioToScreen(rx, ry, rect);
    if (i === 0) ctx.moveTo(s.x, s.y);
    else ctx.lineTo(s.x, s.y);
  }
  ctx.strokeStyle = "rgba(0,220,220,0.6)";
  ctx.lineWidth   = 1.5;
  ctx.stroke();

  _calibPoints.forEach(([rx, ry], i) => {
    const s = ratioToScreen(rx, ry, rect);
    ctx.beginPath();
    ctx.arc(s.x, s.y, DRAW_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = POINT_COLORS[i];
    ctx.fill();
    ctx.fillStyle    = "#fff";
    ctx.font         = "bold 11px monospace";
    ctx.textAlign    = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(POINT_LABELS[i], s.x + DRAW_RADIUS + 3, s.y);
  });
}

function schedulePreview() {
  clearTimeout(_previewTimer);
  _previewTimer = setTimeout(async () => {
    try {
      const res  = await fetch("/api/calibration/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ warp_src: _calibPoints }),
      });
      const data = await res.json();
      if (data.image) elCalibPreview.src = data.image;
    } catch (_) {}
  }, 300);
}

async function enterCalib() {
  try {
    const res  = await fetch("/api/calibration/warp_src");
    const data = await res.json();
    _calibPoints = data.warp_src.map(p => [...p]);
    _calibBackup = data.warp_src.map(p => [...p]);
  } catch (_) { return; }

  _calibActive = true;
  elCalibCanvas.style.display       = "block";
  elCalibCanvas.style.pointerEvents = "auto";
  elCalibControls.style.display     = "block";
  btnCalibrate.style.display        = "none";
  drawCalibCanvas();
  schedulePreview();
}

function exitCalib() {
  _calibActive  = false;
  _dragging     = -1;
  elCalibCanvas.style.display       = "none";
  elCalibCanvas.style.pointerEvents = "none";
  elCalibControls.style.display     = "none";
  btnCalibrate.style.display        = "block";
}

btnCalibrate.addEventListener("click", enterCalib);

btnCancelCalib.addEventListener("click", () => {
  _calibPoints = _calibBackup.map(p => [...p]);
  exitCalib();
});

btnSaveCalib.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/calibration/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ warp_src: _calibPoints }),
    });
    const data = await res.json();
    if (data.ok) exitCalib();
  } catch (_) {}
});

elCalibCanvas.addEventListener("mousedown", (e) => {
  if (!_calibActive) return;
  const rect    = elCalibCanvas.getBoundingClientRect();
  const sx      = e.clientX - rect.left;
  const sy      = e.clientY - rect.top;
  const imgRect = getImageRenderRect();

  for (let i = 0; i < _calibPoints.length; i++) {
    const [rx, ry] = _calibPoints[i];
    const s = ratioToScreen(rx, ry, imgRect);
    const dx = sx - s.x;
    const dy = sy - s.y;
    if (Math.sqrt(dx * dx + dy * dy) <= HIT_RADIUS) {
      _dragging = i;
      break;
    }
  }
});

elCalibCanvas.addEventListener("mousemove", (e) => {
  if (!_calibActive || _dragging === -1) return;
  const rect    = elCalibCanvas.getBoundingClientRect();
  const sx      = e.clientX - rect.left;
  const sy      = e.clientY - rect.top;
  const imgRect = getImageRenderRect();
  const { rx, ry } = screenToRatio(sx, sy, imgRect);
  _calibPoints[_dragging] = [rx, ry];
  drawCalibCanvas();
  schedulePreview();
});

function stopDragging() { _dragging = -1; }
elCalibCanvas.addEventListener("mouseup",    stopDragging);
elCalibCanvas.addEventListener("mouseleave", stopDragging);

// 调试视图
const elDebugToggle = document.getElementById("debug-toggle");
const elDebugWrap   = document.getElementById("debug-wrap");
const elDebugFeed   = document.getElementById("debug-feed");
let _debugTimer = null;

function refreshDebugFrame() {
  elDebugFeed.src = "/api/debug_frame?" + Date.now();
}

elDebugToggle.addEventListener("change", () => {
  if (elDebugToggle.checked) {
    elDebugWrap.style.display = "block";
    refreshDebugFrame();
    _debugTimer = setInterval(refreshDebugFrame, 200);
  } else {
    elDebugWrap.style.display = "none";
    clearInterval(_debugTimer);
    _debugTimer = null;
  }
});

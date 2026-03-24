# cli/canvas_webview.py
"""
Live Canvas — agent-driven visual workspace.

Opens a browser tab showing an interactive HTML5 canvas node-graph
that reflects live task/sub-agent activity in real time.

Each task appears as a **node** on the canvas.  Sub-agents are shown
as child nodes connected by animated directional edges.  Nodes are
colour-coded by status and can be dragged around the canvas.

Run with::

    curie tasks --canvas               # open canvas, live-refresh until Ctrl-C
    curie tasks --canvas --all         # include finished tasks
    curie canvas                       # same as --canvas

The canvas is self-contained (no CDN dependencies) and uses:
  * HTML5 Canvas API for rendering nodes and edges
  * Server-Sent Events (SSE) endpoint ``/events`` for live data push
  * Vanilla JavaScript — no framework
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# tasks.json location (mirrors cli/tasks.py)
# ---------------------------------------------------------------------------
_CURIE_DIR = Path.home() / ".curie"
_TASKS_FILE = _CURIE_DIR / "tasks.json"

_SHUTDOWN_EVENT = threading.Event()


def _load_tasks() -> dict:
    try:
        return json.loads(_TASKS_FILE.read_text())
    except Exception:
        return {"tasks": {}}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curie AI – Live Canvas</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:linear-gradient(135deg,#060b18 0%,#0a1422 60%,#060e1c 100%);
  min-height:100vh;
  font-family:'Segoe UI',system-ui,sans-serif;
  color:#e8f4ff;
  overflow:hidden;
}
#header{
  position:fixed;top:0;left:0;right:0;z-index:10;
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 20px;
  background:rgba(6,11,24,0.85);
  backdrop-filter:blur(8px);
  border-bottom:1px solid rgba(56,189,248,0.2);
}
#header h1{
  font-size:1.1rem;font-weight:700;letter-spacing:.05em;
  background:linear-gradient(90deg,#7dd3fc,#38bdf8,#a78bfa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;
}
#stats{display:flex;gap:12px;font-size:.78rem}
.chip{
  padding:3px 10px;border-radius:12px;font-weight:600;
  background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);
  color:#7dd3fc;
}
.chip.running{background:rgba(16,185,129,0.15);border-color:rgba(16,185,129,0.4);color:#6ee7b7}
.chip.done{background:rgba(100,116,139,0.15);border-color:rgba(100,116,139,0.3);color:#94a3b8}
.chip.failed{background:rgba(239,68,68,0.15);border-color:rgba(239,68,68,0.4);color:#fca5a5}
#controls{
  position:fixed;bottom:16px;left:50%;transform:translateX(-50%);
  z-index:10;display:flex;gap:8px;
}
button{
  padding:6px 14px;border-radius:8px;border:1px solid rgba(56,189,248,0.4);
  background:rgba(56,189,248,0.1);color:#7dd3fc;cursor:pointer;font-size:.8rem;
  transition:background .2s;
}
button:hover{background:rgba(56,189,248,0.25)}
#canvas-wrap{
  position:fixed;inset:44px 0 44px 0;
  overflow:hidden;
}
canvas{display:block;width:100%;height:100%}
#tooltip{
  position:fixed;z-index:20;pointer-events:none;
  background:rgba(10,20,40,0.95);border:1px solid rgba(56,189,248,0.35);
  border-radius:8px;padding:8px 12px;font-size:.78rem;max-width:280px;
  line-height:1.5;display:none;
}
#empty{
  position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  text-align:center;color:#4a6080;pointer-events:none;font-size:.9rem;
  display:none;
}
#status-bar{
  position:fixed;bottom:0;left:0;right:0;height:24px;z-index:10;
  background:rgba(6,11,24,0.8);border-top:1px solid rgba(56,189,248,0.15);
  display:flex;align-items:center;padding:0 12px;gap:8px;font-size:.72rem;color:#4a7090;
}
#status-dot{width:6px;height:6px;border-radius:50%;background:#10b981;
  box-shadow:0 0 6px #10b981;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>
</head>
<body>

<div id="header">
  <h1>⬡ Curie AI — Live Canvas</h1>
  <div id="stats">
    <span class="chip running" id="stat-running">0 running</span>
    <span class="chip done"    id="stat-done">0 done</span>
    <span class="chip failed"  id="stat-failed">0 failed</span>
  </div>
</div>

<div id="canvas-wrap">
  <canvas id="canvas"></canvas>
</div>

<div id="empty">
  <div style="font-size:2rem;margin-bottom:8px">🧩</div>
  <div>No active tasks</div>
  <div style="margin-top:4px;font-size:.8rem">Start a conversation with Curie to see nodes appear</div>
</div>

<div id="controls">
  <button id="btn-fit">Fit view</button>
  <button id="btn-reset">Reset layout</button>
  <button id="btn-toggle-labels">Labels ON</button>
</div>

<div id="tooltip"></div>
<div id="status-bar">
  <span id="status-dot"></span>
  <span id="status-text">Connecting…</span>
</div>

<script>
// ─── Constants ──────────────────────────────────────────────────────────────
const NODE_W = 160, NODE_H = 56;
const CHILD_W = 130, CHILD_H = 44;
const PAD_X = 60, PAD_Y = 80;
const COLS = 4;
const REFRESH_MS = 1200;

// ─── Colors ─────────────────────────────────────────────────────────────────
const COLOR = {
  running: { fill:'#0f3a2e', stroke:'#10b981', text:'#6ee7b7', glow:'rgba(16,185,129,0.4)' },
  done:    { fill:'#1a2235', stroke:'#475569', text:'#94a3b8', glow:'rgba(71,85,105,0.2)' },
  failed:  { fill:'#2d1515', stroke:'#ef4444', text:'#fca5a5', glow:'rgba(239,68,68,0.4)' },
  default: { fill:'#0d1e35', stroke:'#38bdf8', text:'#7dd3fc', glow:'rgba(56,189,248,0.3)' },
};

// ─── State ───────────────────────────────────────────────────────────────────
let tasks = {};
let nodes = [];       // {id, x, y, w, h, task, subId, status, label, desc, isRoot}
let panX = 0, panY = 0, zoom = 1;
let dragging = null, dragOffX = 0, dragOffY = 0;
let showLabels = true;
let hoveredNode = null;
let animTick = 0;

// ─── Canvas setup ────────────────────────────────────────────────────────────
const wrap = document.getElementById('canvas-wrap');
const cvs  = document.getElementById('canvas');
const ctx  = cvs.getContext('2d');

function resize() {
  cvs.width  = wrap.clientWidth;
  cvs.height = wrap.clientHeight;
  draw();
}
window.addEventListener('resize', resize);
resize();

// ─── Layout helpers ──────────────────────────────────────────────────────────
function buildNodes() {
  nodes = [];
  const taskArr = Object.values(tasks).sort((a,b) => (a.started_at||0) - (b.started_at||0));
  let col = 0, row = 0, colHeights = new Array(COLS).fill(0);

  taskArr.forEach(task => {
    const x = col * (NODE_W + PAD_X) + PAD_X;
    const y = colHeights[col] + PAD_Y;
    const existing = nodes.find(n => n.id === task.id && n.isRoot);
    const nx = existing ? existing.x : x;
    const ny = existing ? existing.y : y;

    nodes.push({
      id: task.id, isRoot: true,
      x: nx, y: ny, w: NODE_W, h: NODE_H,
      task, subId: null,
      status: task.status || 'running',
      label: truncate(task.description || task.id, 22),
      desc: task.description || '',
      channel: task.channel || '',
    });

    let childY = ny + NODE_H + 20;
    const agents = Object.values(task.sub_agents || {});
    agents.forEach(agent => {
      const cid = task.id + '::' + agent.id;
      const cExisting = nodes.find(n => n.id === cid);
      const cx = cExisting ? cExisting.x : nx + (NODE_W - CHILD_W) / 2;
      const cy = cExisting ? cExisting.y : childY;
      nodes.push({
        id: cid, isRoot: false,
        x: cx, y: cy, w: CHILD_W, h: CHILD_H,
        task, subId: agent.id,
        status: agent.status || 'running',
        label: truncate(agent.role || agent.id, 18),
        desc: agent.description || '',
        model: agent.model || '',
        result: agent.result_summary || '',
        parentId: task.id,
      });
      childY += CHILD_H + 10;
    });

    colHeights[col] = Math.max(colHeights[col], childY + PAD_Y / 2);
    col = (col + 1) % COLS;
    if (col === 0) row++;
  });
}

function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n-1) + '…' : (s || '');
}

// ─── Drawing ─────────────────────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, cvs.width, cvs.height);
  ctx.save();
  ctx.translate(panX, panY);
  ctx.scale(zoom, zoom);

  // Draw edges first (under nodes)
  drawEdges();

  // Draw nodes
  nodes.forEach(n => drawNode(n));

  ctx.restore();
  animTick++;
}

function drawEdges() {
  nodes.filter(n => !n.isRoot && n.parentId).forEach(child => {
    const parent = nodes.find(p => p.id === child.parentId && p.isRoot);
    if (!parent) return;
    const x1 = parent.x + parent.w / 2;
    const y1 = parent.y + parent.h;
    const x2 = child.x + child.w / 2;
    const y2 = child.y;

    const col = COLOR[child.status] || COLOR.default;
    const grad = ctx.createLinearGradient(x1, y1, x2, y2);
    grad.addColorStop(0, (COLOR[parent.status] || COLOR.default).stroke + '99');
    grad.addColorStop(1, col.stroke + '99');

    ctx.beginPath();
    ctx.moveTo(x1, y1);
    // Bezier curve for a nice organic look
    ctx.bezierCurveTo(x1, y1+20, x2, y2-20, x2, y2);
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([]);
    ctx.stroke();

    // Animated dot travelling along edge for running tasks
    if (child.status === 'running') {
      const t = ((animTick * 0.02) % 1);
      const bx = bezierPoint(x1, x1, x2, x2, t);
      const by = bezierPoint(y1, y1+20, y2-20, y2, t);
      ctx.beginPath();
      ctx.arc(bx, by, 3, 0, Math.PI*2);
      ctx.fillStyle = col.stroke;
      ctx.fill();
    }
  });
}

function bezierPoint(p0, p1, p2, p3, t) {
  const u = 1-t;
  return u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3;
}

function drawNode(n) {
  const col = COLOR[n.status] || COLOR.default;
  const isHovered = hoveredNode && hoveredNode.id === n.id;
  const isRunning = n.status === 'running';

  ctx.save();

  // Glow for running
  if (isRunning || isHovered) {
    const pulse = isRunning ? (Math.sin(animTick * 0.08) + 1) / 2 : 1;
    ctx.shadowColor = isHovered ? '#fff8' : col.glow;
    ctx.shadowBlur = isHovered ? 12 : 8 + pulse * 6;
  }

  // Background
  ctx.beginPath();
  roundRect(ctx, n.x, n.y, n.w, n.h, n.isRoot ? 10 : 7);
  ctx.fillStyle = col.fill;
  ctx.fill();

  // Border
  ctx.strokeStyle = col.stroke;
  ctx.lineWidth = isHovered ? 2 : 1.5;
  ctx.stroke();

  // Status indicator dot
  const dotX = n.x + n.w - 12;
  const dotY = n.y + 12;
  ctx.beginPath();
  ctx.arc(dotX, dotY, 4, 0, Math.PI*2);
  ctx.fillStyle = col.stroke;
  ctx.shadowBlur = 6;
  ctx.shadowColor = col.stroke;
  ctx.fill();
  ctx.shadowBlur = 0;

  if (showLabels) {
    // Label
    ctx.fillStyle = col.text;
    ctx.font = `${n.isRoot ? 600 : 500} ${n.isRoot ? 12 : 11}px 'Segoe UI', sans-serif`;
    ctx.fillText(n.label, n.x + 10, n.y + (n.isRoot ? 22 : 18));

    // Sub-label (role / channel)
    const sub = n.isRoot ? (n.channel ? '📡 ' + n.channel : '') : (n.model ? '🤖 ' + truncate(n.model, 14) : '');
    if (sub) {
      ctx.fillStyle = col.text + '99';
      ctx.font = `400 9.5px 'Segoe UI', sans-serif`;
      ctx.fillText(sub, n.x + 10, n.y + (n.isRoot ? 38 : 32));
    }
  }

  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x+r, y);
  ctx.lineTo(x+w-r, y); ctx.arcTo(x+w, y, x+w, y+r, r);
  ctx.lineTo(x+w, y+h-r); ctx.arcTo(x+w, y+h, x+w-r, y+h, r);
  ctx.lineTo(x+r, y+h); ctx.arcTo(x, y+h, x, y+h-r, r);
  ctx.lineTo(x, y+r); ctx.arcTo(x, y, x+r, y, r);
  ctx.closePath();
}

// ─── Pointer helpers ─────────────────────────────────────────────────────────
function canvasPoint(e) {
  const rect = cvs.getBoundingClientRect();
  return {
    x: (e.clientX - rect.left - panX) / zoom,
    y: (e.clientY - rect.top  - panY) / zoom,
  };
}

function hitTest(px, py) {
  for (let i = nodes.length-1; i >= 0; i--) {
    const n = nodes[i];
    if (px >= n.x && px <= n.x+n.w && py >= n.y && py <= n.y+n.h) return n;
  }
  return null;
}

// ─── Interaction ─────────────────────────────────────────────────────────────
let isPanning = false, panStartX = 0, panStartY = 0;

cvs.addEventListener('mousedown', e => {
  const p = canvasPoint(e);
  const hit = hitTest(p.x, p.y);
  if (hit) {
    dragging = hit;
    dragOffX = p.x - hit.x;
    dragOffY = p.y - hit.y;
  } else {
    isPanning = true;
    panStartX = e.clientX - panX;
    panStartY = e.clientY - panY;
  }
});

cvs.addEventListener('mousemove', e => {
  const p = canvasPoint(e);
  const hit = hitTest(p.x, p.y);
  hoveredNode = hit || null;

  // Tooltip
  const tip = document.getElementById('tooltip');
  if (hit) {
    const lines = [];
    if (hit.isRoot) {
      lines.push(`<b>${hit.task.description || hit.task.id}</b>`);
      lines.push(`Status: <span style="color:${(COLOR[hit.status]||COLOR.default).stroke}">${hit.status}</span>`);
      if (hit.channel) lines.push(`Channel: ${hit.channel}`);
      const agents = Object.values(hit.task.sub_agents||{});
      if (agents.length) lines.push(`Sub-agents: ${agents.length}`);
    } else {
      const agent = (hit.task.sub_agents||{})[hit.subId]||{};
      lines.push(`<b>${agent.role || hit.subId}</b>`);
      lines.push(`Status: <span style="color:${(COLOR[hit.status]||COLOR.default).stroke}">${hit.status}</span>`);
      if (agent.model) lines.push(`Model: ${agent.model}`);
      if (agent.description) lines.push(`Activity: ${agent.description}`);
      if (agent.result_summary) lines.push(`Result: ${agent.result_summary}`);
    }
    tip.innerHTML = lines.join('<br>');
    tip.style.display = 'block';
    tip.style.left = (e.clientX + 14) + 'px';
    tip.style.top  = (e.clientY - 10) + 'px';
  } else {
    tip.style.display = 'none';
  }

  if (dragging) {
    dragging.x = p.x - dragOffX;
    dragging.y = p.y - dragOffY;
  } else if (isPanning) {
    panX = e.clientX - panStartX;
    panY = e.clientY - panStartY;
  }
  cvs.style.cursor = hit ? 'grab' : (isPanning ? 'grabbing' : 'default');
});

cvs.addEventListener('mouseup', () => { dragging = null; isPanning = false; });
cvs.addEventListener('mouseleave', () => {
  document.getElementById('tooltip').style.display = 'none';
  hoveredNode = null;
});

// Zoom with wheel
cvs.addEventListener('wheel', e => {
  e.preventDefault();
  const delta = e.deltaY > 0 ? 0.9 : 1.1;
  const newZoom = Math.max(0.2, Math.min(3, zoom * delta));
  // Zoom toward cursor
  const rect = cvs.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  panX = mx - (mx - panX) * (newZoom / zoom);
  panY = my - (my - panY) * (newZoom / zoom);
  zoom = newZoom;
}, { passive: false });

// ─── Controls ────────────────────────────────────────────────────────────────
document.getElementById('btn-fit').addEventListener('click', fitView);
document.getElementById('btn-reset').addEventListener('click', () => {
  // Redo auto-layout (discard manual drag positions)
  buildNodes();
  fitView();
});
document.getElementById('btn-toggle-labels').addEventListener('click', function() {
  showLabels = !showLabels;
  this.textContent = showLabels ? 'Labels ON' : 'Labels OFF';
});

function fitView() {
  if (!nodes.length) return;
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  nodes.forEach(n => {
    minX = Math.min(minX, n.x);
    minY = Math.min(minY, n.y);
    maxX = Math.max(maxX, n.x + n.w);
    maxY = Math.max(maxY, n.y + n.h);
  });
  const cw = cvs.width, ch = cvs.height;
  const gw = maxX - minX + PAD_X*2;
  const gh = maxY - minY + PAD_Y*2;
  zoom = Math.min(cw/gw, ch/gh, 1.5);
  panX = (cw - gw*zoom) / 2 + PAD_X*zoom - minX*zoom;
  panY = (ch - gh*zoom) / 2 + PAD_Y*zoom - minY*zoom;
}

// ─── Stats ───────────────────────────────────────────────────────────────────
function updateStats(taskArr) {
  const running = taskArr.filter(t => t.status === 'running').length;
  const done    = taskArr.filter(t => t.status === 'done').length;
  const failed  = taskArr.filter(t => t.status === 'failed').length;
  document.getElementById('stat-running').textContent = running + ' running';
  document.getElementById('stat-done').textContent    = done + ' done';
  document.getElementById('stat-failed').textContent  = failed + ' failed';
  const empty = document.getElementById('empty');
  empty.style.display = taskArr.length ? 'none' : 'block';
}

// ─── SSE data feed ───────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource('/events');
  const statusText = document.getElementById('status-text');
  const statusDot  = document.getElementById('status-dot');

  es.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      tasks = data.tasks || {};
      const taskArr = Object.values(tasks);
      buildNodes();
      updateStats(taskArr);
      statusText.textContent = 'Live — last update ' + new Date().toLocaleTimeString();
      statusDot.style.background = '#10b981';
      statusDot.style.boxShadow  = '0 0 6px #10b981';
    } catch(_) {}
  };

  es.onerror = () => {
    statusText.textContent = 'Reconnecting…';
    statusDot.style.background = '#f59e0b';
    statusDot.style.boxShadow  = '0 0 6px #f59e0b';
  };
}

// ─── Animation loop ──────────────────────────────────────────────────────────
function loop() {
  draw();
  requestAnimationFrame(loop);
}

connectSSE();
loop();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        pass  # suppress access logs

    def do_GET(self) -> None:
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/data":
            self._serve_data()
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        data = _HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_data(self) -> None:
        data = json.dumps(_load_tasks()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while not _SHUTDOWN_EVENT.is_set():
                payload = json.dumps(_load_tasks())
                msg = f"data: {payload}\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
                time.sleep(1.2)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def show_canvas(show_finished: bool = False) -> None:  # noqa: ARG001
    """
    Start the Live Canvas HTTP server and open it in the default browser.

    Blocks until the user presses Ctrl-C.
    """
    _SHUTDOWN_EVENT.clear()
    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"

    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich import box

        console = Console()
        console.print(
            Panel(
                f"[bold cyan]Live Canvas[/bold cyan] running at [link={url}]{url}[/link]\n"
                f"[dim]Drag nodes · Scroll to zoom · Hover for details[/dim]\n"
                f"[dim]Press Ctrl-C to stop[/dim]",
                box=box.ROUNDED,
                border_style="cyan",
                title="⬡ Curie AI",
            )
        )
    except ImportError:
        print(f"\n⬡ Live Canvas at {url}\nPress Ctrl-C to stop.\n")

    webbrowser.open(url)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _SHUTDOWN_EVENT.set()
        server.shutdown()
        server.server_close()

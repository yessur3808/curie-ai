# cli/agent_webview.py
"""
Browser-based animated visualization for Curie AI tasks and sub-agents.

Run with:
    curie tasks --web            # open browser, live-refresh until Ctrl-C
    curie tasks --web --all      # include finished tasks

What it does
------------
* Starts a tiny HTTP server on a free localhost port.
* Serves an HTML page with:
    - An animated SVG portrait of Curie (wavy blue hair, teal eyes, rosy
      cheeks, red lips, dark outfit) with idle / thinking / working CSS
      keyframe states.
    - A sub-agent panel for every registered sub-agent, each with a
      role-specific animated avatar and a live activity tooltip on hover.
    - Server-Sent Events (SSE) endpoint /events that pushes fresh task JSON
      every second so the page updates without polling.
* Automatically opens the default browser.
* Gracefully shuts down when the user presses Ctrl-C.

No external Python dependencies beyond the standard library.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
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
# HTML page (self-contained, no CDN dependencies)
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curie AI – Agent Dashboard</title>
<style>
/* ── Reset & base ──────────────────────────────────────────────────────── */
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:linear-gradient(135deg,#0a0e1a 0%,#0d1b2e 60%,#0a1628 100%);
  min-height:100vh;
  font-family:'Segoe UI',system-ui,sans-serif;
  color:#e8f4ff;
  overflow-x:hidden;
}

/* ── Stars background ──────────────────────────────────────────────────── */
#stars{position:fixed;inset:0;pointer-events:none;z-index:0}
.star{position:absolute;border-radius:50%;background:#fff;animation:twinkle var(--d,3s) infinite var(--delay,0s)}
@keyframes twinkle{0%,100%{opacity:.2}50%{opacity:1}}

/* ── Layout ────────────────────────────────────────────────────────────── */
#app{position:relative;z-index:1;max-width:1200px;margin:0 auto;padding:24px 16px}
header{text-align:center;margin-bottom:32px}
header h1{
  font-size:2rem;font-weight:700;letter-spacing:.04em;
  background:linear-gradient(90deg,#7dd3fc,#38bdf8,#0ea5e9,#a78bfa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;
}
header p{color:#94a3b8;margin-top:6px;font-size:.9rem}
#summary{
  display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin-bottom:32px;
}
.stat-chip{
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
  border-radius:999px;padding:6px 18px;font-size:.85rem;color:#94a3b8;
}
.stat-chip span{color:#7dd3fc;font-weight:700;font-size:1rem}

/* ── Task card ─────────────────────────────────────────────────────────── */
.task-card{
  background:rgba(255,255,255,.04);
  border:1px solid rgba(100,180,255,.15);
  border-radius:20px;padding:24px;margin-bottom:28px;
  box-shadow:0 4px 32px rgba(0,0,0,.4);
  transition:border-color .3s;
}
.task-card.running{border-color:rgba(74,222,128,.35)}
.task-card.done{border-color:rgba(56,189,248,.25)}
.task-card.failed{border-color:rgba(248,113,113,.35)}
.task-header{
  display:flex;align-items:center;gap:14px;margin-bottom:22px;flex-wrap:wrap;
}
.task-status-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.running .task-status-dot{background:#4ade80;box-shadow:0 0 8px #4ade80;animation:pulse-dot 1.4s infinite}
.done .task-status-dot{background:#38bdf8}
.failed .task-status-dot{background:#f87171}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.3}}
.task-desc{font-size:1.05rem;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.task-meta{color:#64748b;font-size:.8rem;margin-left:auto;white-space:nowrap}

/* ── Agent grid ────────────────────────────────────────────────────────── */
.agents-row{
  display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;
}

/* ── Main Curie avatar ─────────────────────────────────────────────────── */
.curie-avatar-wrap{
  display:flex;flex-direction:column;align-items:center;gap:10px;
  flex-shrink:0;
}
.curie-avatar-wrap .label{
  font-size:.8rem;color:#7dd3fc;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
}

/* ── Sub-agent card ────────────────────────────────────────────────────── */
.sub-agents-grid{
  display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start;flex:1;
}
.sub-card{
  background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.1);
  border-radius:14px;padding:12px 10px;
  display:flex;flex-direction:column;align-items:center;gap:8px;
  width:110px;cursor:pointer;transition:all .25s;position:relative;
}
.sub-card:hover{border-color:rgba(125,211,252,.5);background:rgba(125,211,252,.08);transform:translateY(-3px)}
.sub-card.running{border-color:rgba(74,222,128,.45);background:rgba(74,222,128,.06)}
.sub-card.done-handled{border-color:rgba(56,189,248,.35);background:rgba(56,189,248,.04)}
.sub-card.done-skipped{opacity:.45}
.sub-card.failed{border-color:rgba(248,113,113,.4)}
.sub-card .role-label{font-size:.72rem;font-weight:600;color:#94a3b8;text-align:center;letter-spacing:.03em}
.sub-card .status-badge{
  font-size:.68rem;padding:2px 10px;border-radius:999px;font-weight:600;
}
.running .status-badge{background:rgba(74,222,128,.2);color:#4ade80}
.done-handled .status-badge{background:rgba(56,189,248,.2);color:#38bdf8}
.done-skipped .status-badge{background:rgba(255,255,255,.08);color:#64748b}
.failed .status-badge{background:rgba(248,113,113,.2);color:#f87171}
.sub-card .dur{font-size:.68rem;color:#475569}

/* ── Tooltip on hover ──────────────────────────────────────────────────── */
.tooltip{
  display:none;position:absolute;bottom:calc(100% + 8px);left:50%;
  transform:translateX(-50%);
  background:rgba(2,8,25,.92);border:1px solid rgba(125,211,252,.3);
  color:#e2e8f0;font-size:.78rem;padding:8px 12px;
  border-radius:10px;white-space:normal;width:200px;text-align:center;
  pointer-events:none;z-index:100;
  box-shadow:0 8px 24px rgba(0,0,0,.6);
}
.sub-card:hover .tooltip{display:block}

/* ── "no tasks" empty state ────────────────────────────────────────────── */
#empty{
  text-align:center;padding:60px 20px;color:#334155;
}
#empty svg{margin:0 auto 20px;display:block}
#empty h2{font-size:1.3rem;margin-bottom:8px;color:#475569}

/* ── Footer ────────────────────────────────────────────────────────────── */
footer{text-align:center;padding:24px 0 8px;color:#1e3a5f;font-size:.78rem}

/* ══════════════════════════════════════════════════════════════════════════
   SVG ANIMATIONS
   ══════════════════════════════════════════════════════════════════════════ */

/* Hair wave */
@keyframes hairWave{
  0%,100%{d:path("M20,45 Q40,15 60,40 Q80,65 100,35 Q120,10 140,38")}
  33%{d:path("M20,45 Q42,12 62,42 Q82,68 102,33 Q122,8 142,40")}
  66%{d:path("M20,45 Q38,18 58,38 Q78,62 98,37 Q118,12 138,36")}
}
@keyframes hairWave2{
  0%,100%{d:path("M20,50 Q45,20 70,50 Q95,80 120,45")}
  50%{d:path("M20,50 Q43,23 68,53 Q93,83 118,42")}
}
/* Blink */
@keyframes blink{0%,90%,100%{transform:scaleY(1)}95%{transform:scaleY(.05)}}
/* Breathe / idle float */
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
/* Thinking wobble */
@keyframes think{0%,100%{transform:rotate(0deg)}25%{transform:rotate(-3deg)}75%{transform:rotate(3deg)}}
/* Working glow pulse */
@keyframes glow{0%,100%{filter:drop-shadow(0 0 8px rgba(125,211,252,.4))}50%{filter:drop-shadow(0 0 20px rgba(125,211,252,.9))}}
/* Sparkle orbit around head */
@keyframes orbit{0%{transform:rotate(0deg) translateX(52px) rotate(0deg)}100%{transform:rotate(360deg) translateX(52px) rotate(-360deg)}}
@keyframes orbit2{0%{transform:rotate(120deg) translateX(52px) rotate(-120deg)}100%{transform:rotate(480deg) translateX(52px) rotate(-480deg)}}
@keyframes orbit3{0%{transform:rotate(240deg) translateX(52px) rotate(-240deg)}100%{transform:rotate(600deg) translateX(52px) rotate(-600deg)}}
/* Thought bubble */
@keyframes thoughtPop{0%,100%{opacity:0;transform:scale(.5)}20%,80%{opacity:1;transform:scale(1)}}

/* Sub-agent avatar animations */
@keyframes subFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}
@keyframes subBlink{0%,88%,100%{transform:scaleY(1)}94%{transform:scaleY(.08)}}
@keyframes subSpin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
@keyframes subPulse{0%,100%{opacity:1}50%{opacity:.5}}
@keyframes sparkle{0%,100%{opacity:0;transform:scale(0)}50%{opacity:1;transform:scale(1)}}
</style>
</head>
<body>

<canvas id="stars" aria-hidden="true"></canvas>

<div id="app">
  <header>
    <h1>✨ Curie AI – Agent Dashboard</h1>
    <p>Live view of tasks and sub-agents&nbsp;·&nbsp;<span id="conn-status" style="color:#4ade80">● connected</span></p>
  </header>

  <div id="summary">
    <div class="stat-chip">Tasks&nbsp;<span id="s-running">0</span>&thinsp;running / <span id="s-total">0</span>&thinsp;total</div>
    <div class="stat-chip">Sub-agents&nbsp;<span id="s-agents-running">0</span>&thinsp;running / <span id="s-agents-total">0</span>&thinsp;total</div>
    <div class="stat-chip" id="s-uptime" style="display:none">⏱ <span id="s-uptime-val">0s</span></div>
  </div>

  <div id="tasks-root"></div>

  <div id="empty" style="display:none">
    <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
      <circle cx="40" cy="40" r="38" stroke="#1e3a5f" stroke-width="2"/>
      <path d="M25 35 Q40 20 55 35" stroke="#1e3a5f" stroke-width="2" fill="none"/>
      <circle cx="28" cy="38" r="4" fill="#1e3a5f"/>
      <circle cx="52" cy="38" r="4" fill="#1e3a5f"/>
      <path d="M28 54 Q40 48 52 54" stroke="#1e3a5f" stroke-width="2" fill="none"/>
    </svg>
    <h2>No active tasks</h2>
    <p>Curie is idle and waiting for messages…</p>
  </div>
</div>

<footer>Curie AI &mdash; Agent Dashboard &mdash; auto-refreshing via SSE</footer>

<script>
/* ══════════════════════════════════════════════════════════════════════════
   Stars background
   ══════════════════════════════════════════════════════════════════════════ */
(function(){
  const canvas=document.getElementById('stars'),ctx=canvas.getContext('2d');
  let stars=[];
  function resize(){canvas.width=window.innerWidth;canvas.height=window.innerHeight;stars=[]}
  function mkStars(){
    for(let i=0;i<180;i++){
      stars.push({x:Math.random()*canvas.width,y:Math.random()*canvas.height,
        r:Math.random()*1.4+.3,a:Math.random(),da:(Math.random()-.5)*.012});
    }
  }
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    stars.forEach(s=>{
      s.a=Math.max(.1,Math.min(1,s.a+s.da));
      if(s.a<=.1||s.a>=1)s.da*=-1;
      ctx.globalAlpha=s.a;ctx.fillStyle='#fff';
      ctx.beginPath();ctx.arc(s.x,s.y,s.r,0,Math.PI*2);ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  window.addEventListener('resize',()=>{resize();mkStars()});
  resize();mkStars();draw();
})();

/* ══════════════════════════════════════════════════════════════════════════
   SVG avatar builders
   ══════════════════════════════════════════════════════════════════════════ */

/**
 * Build the main Curie SVG – animated portrait.
 * status: 'running' | 'done' | 'failed' | 'idle'
 */
function buildCurieSVG(status){
  const isWorking = status==='running';
  const isDone    = status==='done';
  const isFailed  = status==='failed';

  /* animation classes for the whole figure */
  const figAnim = isWorking ? 'animation:glow 2s infinite,float 3s ease-in-out infinite'
                : isDone    ? 'animation:float 4s ease-in-out infinite'
                : isFailed  ? ''
                : 'animation:float 5s ease-in-out infinite';

  /* eye expression */
  const eyeExpr = isDone   ? '◡' : isFailed ? '⊙' : '◉';
  const mouthPath= isDone  ? 'M52,108 Q62,116 72,108'
                : isFailed ? 'M52,110 Q62,104 72,110'
                : 'M52,108 Q62,114 72,108';

  /* sparkles (show when running) */
  const sparkles = isWorking ? `
    <g style="transform-origin:62px 62px">
      <circle cx="62" cy="62" r="4" fill="#fde68a" opacity=".9" style="animation:orbit 2.8s linear infinite"/>
    </g>
    <g style="transform-origin:62px 62px">
      <circle cx="62" cy="62" r="3" fill="#a78bfa" opacity=".8" style="animation:orbit2 2.8s linear infinite"/>
    </g>
    <g style="transform-origin:62px 62px">
      <circle cx="62" cy="62" r="2.5" fill="#7dd3fc" opacity=".7" style="animation:orbit3 2.8s linear infinite"/>
    </g>` : '';

  /* thought bubble (show when status is running) */
  const thoughtBubble = isWorking ? `
    <g style="transform-origin:105px 28px;animation:thoughtPop 3s ease-in-out infinite">
      <circle cx="96" cy="42" r="4" fill="rgba(125,211,252,.25)" stroke="rgba(125,211,252,.4)" stroke-width="1"/>
      <circle cx="101" cy="34" r="5" fill="rgba(125,211,252,.25)" stroke="rgba(125,211,252,.4)" stroke-width="1"/>
      <rect x="94" y="22" width="22" height="16" rx="8"
            fill="rgba(125,211,252,.18)" stroke="rgba(125,211,252,.5)" stroke-width="1"/>
      <text x="105" y="33" text-anchor="middle" font-size="9" fill="#7dd3fc">?</text>
    </g>` : '';

  return `<svg viewBox="0 0 124 160" width="124" height="160" xmlns="http://www.w3.org/2000/svg"
           style="${figAnim};display:block">

  <!-- defs: hair gradient, skin, blush -->
  <defs>
    <radialGradient id="skinGrad" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#fde8cc"/>
      <stop offset="100%" stop-color="#f5c89a"/>
    </radialGradient>
    <radialGradient id="blushGrad" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#f9a8d4" stop-opacity=".7"/>
      <stop offset="100%" stop-color="#f9a8d4" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="hairGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1d4ed8"/>
      <stop offset="40%" stop-color="#2563eb"/>
      <stop offset="70%" stop-color="#3b82f6"/>
      <stop offset="100%" stop-color="#60a5fa"/>
    </linearGradient>
    <linearGradient id="hairShine" x1="20%" y1="0%" x2="80%" y2="100%">
      <stop offset="0%" stop-color="#93c5fd" stop-opacity=".6"/>
      <stop offset="100%" stop-color="#1d4ed8" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="outfitGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1e293b"/>
      <stop offset="100%" stop-color="#0f172a"/>
    </linearGradient>
    <filter id="softBlur"><feGaussianBlur stdDeviation="1.2"/></filter>
  </defs>

  ${sparkles}
  ${thoughtBubble}

  <!-- ── Back hair (behind face) ── -->
  <ellipse cx="62" cy="62" rx="44" ry="50" fill="url(#hairGrad)" opacity=".95"/>
  <!-- flowing waves back -->
  <path d="M18,55 Q10,80 14,105 Q18,130 26,145 Q30,152 35,148" fill="url(#hairGrad)" opacity=".85"/>
  <path d="M106,55 Q114,80 110,105 Q106,130 98,145 Q94,152 89,148" fill="url(#hairGrad)" opacity=".85"/>

  <!-- ── Neck ── -->
  <rect x="52" y="110" width="20" height="18" rx="6" fill="url(#skinGrad)"/>

  <!-- ── Outfit / shoulders ── -->
  <path d="M20,158 Q28,128 52,122 Q62,120 72,122 Q96,128 104,158 Z"
        fill="url(#outfitGrad)"/>
  <!-- collar -->
  <path d="M52,122 Q62,136 72,122" fill="none" stroke="#334155" stroke-width="2"/>
  <!-- outfit highlight -->
  <path d="M40,140 Q50,135 62,136 Q74,135 84,140" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="1.5"/>

  <!-- ── Face ── -->
  <ellipse cx="62" cy="76" rx="36" ry="38" fill="url(#skinGrad)"/>
  <!-- face shadow -->
  <ellipse cx="62" cy="76" rx="36" ry="38" fill="rgba(200,140,80,.08)"/>

  <!-- ── Blush ── -->
  <ellipse cx="38" cy="88" rx="10" ry="7" fill="url(#blushGrad)" filter="url(#softBlur)"/>
  <ellipse cx="86" cy="88" rx="10" ry="7" fill="url(#blushGrad)" filter="url(#softBlur)"/>

  <!-- ── Brows ── -->
  <path d="M42,64 Q48,61 54,63" stroke="#92400e" stroke-width="2.2" fill="none" stroke-linecap="round"/>
  <path d="M70,63 Q76,61 82,64" stroke="#92400e" stroke-width="2.2" fill="none" stroke-linecap="round"/>

  <!-- ── Eyes ── -->
  <g style="transform-origin:48px 74px;animation:blink 4s ease-in-out infinite">
    <ellipse cx="48" cy="74" rx="8" ry="9" fill="white"/>
    <ellipse cx="48" cy="75" rx="6.5" ry="7.5" fill="#0891b2"/>
    <ellipse cx="48" cy="75" rx="4" ry="4.5" fill="#0c4a6e"/>
    <circle cx="45" cy="72" r="1.8" fill="white"/>
    <circle cx="50" cy="73" r="1" fill="white" opacity=".7"/>
  </g>
  <g style="transform-origin:76px 74px;animation:blink 4s ease-in-out infinite 0.1s">
    <ellipse cx="76" cy="74" rx="8" ry="9" fill="white"/>
    <ellipse cx="76" cy="75" rx="6.5" ry="7.5" fill="#0891b2"/>
    <ellipse cx="76" cy="75" rx="4" ry="4.5" fill="#0c4a6e"/>
    <circle cx="73" cy="72" r="1.8" fill="white"/>
    <circle cx="78" cy="73" r="1" fill="white" opacity=".7"/>
  </g>
  <!-- eyelashes -->
  <path d="M40,68 L38,65 M43,67 L42,64 M46,66.5 L46,63" stroke="#1e293b" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M68,67 L66,64 M71,67 L71,64 M74,67.5 L76,64 M78,68 L80,65" stroke="#1e293b" stroke-width="1.2" stroke-linecap="round"/>

  <!-- ── Nose ── -->
  <path d="M60,84 Q62,89 64,84" stroke="#d97706" stroke-width="1.3" fill="none" stroke-linecap="round" opacity=".5"/>

  <!-- ── Mouth ── -->
  <path d="${mouthPath}" stroke="#dc2626" stroke-width="2.5" fill="none" stroke-linecap="round"/>
  <!-- lips fill -->
  <path d="M52,108 Q57,111 62,110 Q67,111 72,108 Q67,113 62,114 Q57,113 52,108 Z"
        fill="#ef4444" opacity=".7"/>

  <!-- ── Front hair (over face top) ── -->
  <!-- Main voluminous top hair -->
  <path d="M20,62 Q22,28 42,22 Q62,14 82,22 Q102,28 104,62"
        fill="url(#hairGrad)"/>
  <!-- Hair shine -->
  <path d="M30,58 Q35,30 58,22 Q72,18 80,28 Q60,20 38,30 Q22,44 26,62 Z"
        fill="url(#hairShine)" opacity=".5"/>
  <!-- Flowing side locks -->
  <path d="M20,62 Q12,90 16,118 Q20,134 28,142"
        fill="url(#hairGrad)" stroke="url(#hairGrad)" stroke-width="1"/>
  <path d="M104,62 Q112,90 108,118 Q104,134 96,142"
        fill="url(#hairGrad)" stroke="url(#hairGrad)" stroke-width="1"/>
  <!-- Hair detail waves (animated) -->
  <path d="M22,72 Q32,60 44,72 Q56,84 68,70" fill="none" stroke="#60a5fa" stroke-width="1.5" opacity=".5"
        style="animation:hairWave 4s ease-in-out infinite"/>
  <path d="M18,84 Q30,70 44,84 Q58,98 72,82" fill="none" stroke="#93c5fd" stroke-width="1.2" opacity=".4"
        style="animation:hairWave 4s ease-in-out infinite .6s"/>
  <path d="M100,72 Q92,58 80,72 Q68,84 56,70" fill="none" stroke="#60a5fa" stroke-width="1.5" opacity=".5"
        style="animation:hairWave 4s ease-in-out infinite .3s"/>

  <!-- ── Ear ── -->
  <ellipse cx="26" cy="80" rx="5" ry="7" fill="url(#skinGrad)"/>
  <ellipse cx="98" cy="80" rx="5" ry="7" fill="url(#skinGrad)"/>
  <!-- earring -->
  <circle cx="26" cy="87" r="2.5" fill="#fde68a" stroke="#f59e0b" stroke-width=".8"/>
  <circle cx="98" cy="87" r="2.5" fill="#fde68a" stroke="#f59e0b" stroke-width=".8"/>

  <!-- ── Status indicator dot ── -->
  ${isWorking ? '<circle cx="110" cy="14" r="6" fill="#4ade80"><animate attributeName="r" values="6;8;6" dur="1.2s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;.5;1" dur="1.2s" repeatCount="indefinite"/></circle>' : ''}
  ${isDone ? '<circle cx="110" cy="14" r="6" fill="#38bdf8"/>' : ''}
  ${isFailed ? '<circle cx="110" cy="14" r="6" fill="#f87171"/>' : ''}

</svg>`;
}

/* ─────────────────────────────────────────────────────────────────────────
   Sub-agent avatar SVGs (role-specific look)
   ───────────────────────────────────────────────────────────────────────── */
const SUB_CONFIGS = {
  coding_assistant: {hairColor:'#7c3aed',eyeColor:'#a78bfa',accent:'#c4b5fd',badge:'💻',glasses:true},
  navigation:       {hairColor:'#065f46',eyeColor:'#34d399',accent:'#6ee7b7',badge:'🗺',arrow:true},
  scheduler:        {hairColor:'#92400e',eyeColor:'#fbbf24',accent:'#fde68a',badge:'⏰'},
  trip_planner:     {hairColor:'#be185d',eyeColor:'#f472b6',accent:'#fbcfe8',badge:'✈',shades:true},
  llm_inference:    {hairColor:'#0c4a6e',eyeColor:'#38bdf8',accent:'#7dd3fc',badge:'🧠',sparkleEyes:true},
  system_commands:  {hairColor:'#78350f',eyeColor:'#fb923c',accent:'#fed7aa',badge:'⚙'},
};
const DEFAULT_SUB = {hairColor:'#374151',eyeColor:'#9ca3af',accent:'#d1d5db',badge:'?'};

function buildSubSVG(role, status){
  const cfg = SUB_CONFIGS[role] || DEFAULT_SUB;
  const isRunning = status==='running';
  const isDone    = status==='done-handled';
  const isSkipped = status==='done-skipped';
  const isFailed  = status==='failed';

  const opacity = isSkipped ? '.35' : '1';
  const figStyle = isRunning ? 'animation:subFloat 2.5s ease-in-out infinite'
                 : isDone    ? 'animation:subFloat 4s ease-in-out infinite'
                 : '';

  /* Eyes */
  let eyeL,eyeR;
  if(cfg.glasses){
    eyeL=`<rect x="10" y="16" width="10" height="8" rx="3" fill="none" stroke="${cfg.accent}" stroke-width="1.5"/>
           <ellipse cx="15" cy="20" rx="3.5" ry="3.5" fill="${cfg.eyeColor}"/>`;
    eyeR=`<rect x="24" y="16" width="10" height="8" rx="3" fill="none" stroke="${cfg.accent}" stroke-width="1.5"/>
           <ellipse cx="29" cy="20" rx="3.5" ry="3.5" fill="${cfg.eyeColor}"/>
           <line x1="20" y1="20" x2="24" y2="20" stroke="${cfg.accent}" stroke-width="1"/>`;
  } else if(cfg.shades){
    eyeL=`<rect x="10" y="17" width="11" height="6" rx="3" fill="${cfg.eyeColor}" opacity=".8"/>`;
    eyeR=`<rect x="23" y="17" width="11" height="6" rx="3" fill="${cfg.eyeColor}" opacity=".8"/>
          <line x1="21" y1="20" x2="23" y2="20" stroke="${cfg.eyeColor}" stroke-width=".8"/>`;
  } else if(cfg.sparkleEyes){
    eyeL=`<circle cx="15" cy="20" r="4" fill="${cfg.eyeColor}"/>
          <circle cx="13" cy="18" r="1.2" fill="white" opacity=".9"/>
          ${isRunning?`<circle cx="15" cy="20" r="2" fill="white" opacity=".4" style="animation:sparkle 1s infinite .1s"/>`:``}`;
    eyeR=`<circle cx="29" cy="20" r="4" fill="${cfg.eyeColor}"/>
          <circle cx="27" cy="18" r="1.2" fill="white" opacity=".9"/>
          ${isRunning?`<circle cx="29" cy="20" r="2" fill="white" opacity=".4" style="animation:sparkle 1s infinite .4s"/>`:``}`;
  } else if(cfg.arrow){
    eyeL=`<path d="M11,22 L15,18 L19,22" fill="${cfg.eyeColor}" stroke="${cfg.eyeColor}" stroke-width=".5"/>`;
    eyeR=`<path d="M23,18 L27,22 L31,18" fill="${cfg.eyeColor}" stroke="${cfg.eyeColor}" stroke-width=".5"/>`;
  } else {
    eyeL=`<circle cx="15" cy="20" r="4" fill="${cfg.eyeColor}"/>
          <circle cx="13" cy="18" r="1.2" fill="white" opacity=".8"/>`;
    eyeR=`<circle cx="29" cy="20" r="4" fill="${cfg.eyeColor}"/>
          <circle cx="27" cy="18" r="1.2" fill="white" opacity=".8"/>`;
  }

  const blinkStyle = `style="transform-origin:22px 20px;animation:subBlink ${3+Math.random()*2}s ease-in-out infinite"`;

  /* Mouth */
  const mouthPath = isDone   ? 'M14,30 Q22,35 30,30'
                  : isFailed ? 'M14,32 Q22,27 30,32'
                  : isRunning? 'M14,30 Q22,34 30,30'
                  :            'M16,30 Q22,33 28,30';

  /* Spinner badge for running */
  const badge = isRunning
    ? `<circle cx="34" cy="8" r="5" fill="${cfg.hairColor}" stroke="${cfg.accent}" stroke-width="1.2">
         <animate attributeName="r" values="5;6.5;5" dur="1s" repeatCount="indefinite"/>
         <animate attributeName="opacity" values="1;.6;1" dur="1s" repeatCount="indefinite"/>
       </circle>`
    : '';

  return `<svg viewBox="0 0 44 58" width="64" height="74" xmlns="http://www.w3.org/2000/svg"
           style="${figStyle};display:block;opacity:${opacity}">
  <defs>
    <radialGradient id="sk${role}" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#fde8cc"/><stop offset="100%" stop-color="#f5c89a"/>
    </radialGradient>
  </defs>

  ${badge}

  <!-- Hair -->
  <ellipse cx="22" cy="18" rx="17" ry="18" fill="${cfg.hairColor}" opacity=".9"/>
  <path d="M5,18 Q4,32 7,42" fill="${cfg.hairColor}" opacity=".8"/>
  <path d="M39,18 Q40,32 37,42" fill="${cfg.hairColor}" opacity=".8"/>
  <path d="M7,16 Q10,6 22,4 Q34,6 37,16" fill="${cfg.hairColor}"/>
  <!-- hair shine -->
  <path d="M10,14 Q14,6 22,4 Q28,5 30,10 Q22,6 14,10 Z" fill="rgba(255,255,255,.2)"/>

  <!-- Neck -->
  <rect x="17" y="35" width="10" height="7" rx="3" fill="url(#sk${role})"/>

  <!-- Body -->
  <path d="M4,57 Q8,44 17,41 Q22,40 27,41 Q36,44 40,57 Z" fill="${cfg.hairColor}" opacity=".85"/>

  <!-- Face -->
  <ellipse cx="22" cy="22" rx="14" ry="15" fill="url(#sk${role})"/>

  <!-- Blush -->
  <ellipse cx="10" cy="27" rx="5" ry="3.5" fill="#f9a8d4" opacity=".35"/>
  <ellipse cx="34" cy="27" rx="5" ry="3.5" fill="#f9a8d4" opacity=".35"/>

  <!-- Eyes (blinking group) -->
  <g ${blinkStyle}>${eyeL}${eyeR}</g>

  <!-- Mouth -->
  <path d="${mouthPath}" stroke="#dc2626" stroke-width="1.8" fill="none" stroke-linecap="round"/>
</svg>`;
}

/* ══════════════════════════════════════════════════════════════════════════
   DOM rendering
   ══════════════════════════════════════════════════════════════════════════ */

const ROLE_LABELS = {
  coding_assistant:'💻 Coding',
  navigation:      '🗺 Navigation',
  scheduler:       '⏰ Scheduler',
  trip_planner:    '✈ Trip Planner',
  llm_inference:   '🧠 LLM Inference',
  system_commands: '⚙ System',
};

function fmt_age(ts){
  if(!ts) return '—';
  const s=Math.floor(Date.now()/1000-ts);
  if(s<60)return s+'s';
  if(s<3600)return Math.floor(s/60)+'m '+s%60+'s';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}
function fmt_dur(start,end){
  if(!start) return '—';
  const s=Math.floor((end||Date.now()/1000)-start);
  if(s<60)return s+'s';
  return Math.floor(s/60)+'m '+s%60+'s';
}

function subCardClass(ag){
  const st=ag.status;
  if(st==='running') return 'sub-card running';
  if(st==='done'){
    return ag.result_summary==='skipped' ? 'sub-card done-skipped' : 'sub-card done-handled';
  }
  if(st==='failed') return 'sub-card failed';
  return 'sub-card';
}
function subStatus(ag){
  if(ag.status==='running'){
    return 'sub-card running';
  }
  if(ag.status==='done') return ag.result_summary==='skipped'?'done-skipped':'done-handled';
  return ag.status||'idle';
}
function subBadgeLabel(ag){
  if(ag.status==='running') return '⠋ running';
  if(ag.status==='done' && ag.result_summary==='skipped') return 'skipped';
  if(ag.status==='done') return '✔ done';
  if(ag.status==='failed') return '✖ failed';
  return ag.status||'—';
}

function renderTasks(data, showAll){
  const root = document.getElementById('tasks-root');
  const empty = document.getElementById('empty');
  let tasks = Object.values(data.tasks||{});
  tasks.sort((a,b)=>(b.started_at||0)-(a.started_at||0));
  if(!showAll) tasks=tasks.filter(t=>t.status==='running');

  // Summary
  const running=tasks.filter(t=>t.status==='running').length;
  document.getElementById('s-running').textContent=running;
  document.getElementById('s-total').textContent=tasks.length;
  let ra=0,ta=0;
  tasks.forEach(t=>{
    const subs=Object.values(t.sub_agents||{});
    ta+=subs.length;
    ra+=subs.filter(a=>a.status==='running').length;
  });
  document.getElementById('s-agents-running').textContent=ra;
  document.getElementById('s-agents-total').textContent=ta;

  if(!tasks.length){
    root.innerHTML='';
    empty.style.display='';
    return;
  }
  empty.style.display='none';

  const html=tasks.map(task=>{
    const st=task.status||'running';
    const subs=Object.values(task.sub_agents||{});
    const age=fmt_age(task.started_at);
    const desc=(task.description||'').substring(0,80);

    const subCards=subs.map(ag=>{
      const svgStatus=ag.status==='done'?(ag.result_summary==='skipped'?'done-skipped':'done-handled'):ag.status;
      const tooltip=ag.description||ag.result_summary||ag.role||'';
      const dur=fmt_dur(ag.started_at,ag.finished_at);
      return `<div class="${subCardClass(ag)}" title="">
        ${buildSubSVG(ag.role||'',svgStatus)}
        <div class="role-label">${ROLE_LABELS[ag.role]||ag.role||'?'}</div>
        <div class="status-badge">${subBadgeLabel(ag)}</div>
        <div class="dur">${dur}</div>
        <div class="tooltip">${tooltip}</div>
      </div>`;
    }).join('');

    return `<div class="task-card ${st}">
      <div class="task-header">
        <div class="task-status-dot"></div>
        <div class="task-desc" title="${desc}">${desc||'(no description)'}</div>
        <div class="task-meta">#${(task.id||'?').substring(0,8)} &middot; ${task.channel||'?'} &middot; ${age}</div>
      </div>
      <div class="agents-row">
        <div class="curie-avatar-wrap">
          ${buildCurieSVG(st)}
          <div class="label">✨ Curie</div>
        </div>
        <div class="sub-agents-grid">${subCards}</div>
      </div>
    </div>`;
  }).join('');

  root.innerHTML=html;
}

/* ══════════════════════════════════════════════════════════════════════════
   SSE live updates
   ══════════════════════════════════════════════════════════════════════════ */
const SHOW_ALL = window.location.search.includes('all=1');
let startedAt = Date.now();

function connectSSE(){
  const es=new EventSource('/events');
  es.onmessage=function(e){
    try{
      const data=JSON.parse(e.data);
      renderTasks(data,SHOW_ALL);
      // uptime
      const secs=Math.floor((Date.now()-startedAt)/1000);
      const upEl=document.getElementById('s-uptime');
      upEl.style.display='';
      document.getElementById('s-uptime-val').textContent=
        secs<60?secs+'s':Math.floor(secs/60)+'m '+secs%60+'s';
    }catch(err){console.error(err)}
  };
  es.onerror=function(){
    document.getElementById('conn-status').textContent='● reconnecting…';
    document.getElementById('conn-status').style.color='#fbbf24';
    setTimeout(connectSSE,3000);
    es.close();
  };
  es.onopen=function(){
    document.getElementById('conn-status').textContent='● connected';
    document.getElementById('conn-status').style.color='#4ade80';
  };
}
connectSSE();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    show_all: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:  # silence access log
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/":
            self._send_html(_HTML)

        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while not _SHUTDOWN_EVENT.is_set():
                    data = _load_tasks()
                    payload = json.dumps(data)
                    self.wfile.write(
                        f"data: {payload}\n\n".encode()
                    )
                    self.wfile.flush()
                    _SHUTDOWN_EVENT.wait(timeout=1.0)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif path == "/data":
            body = json.dumps(_load_tasks()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def show_web(show_finished: bool = False) -> None:
    """
    Start a local HTTP server and open the animated Curie dashboard in the
    default browser.  Runs until the user presses Ctrl-C.
    """
    _SHUTDOWN_EVENT.clear()

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}" + ("?all=1" if show_finished else "")

    server = HTTPServer(("127.0.0.1", port), _Handler)
    _Handler.show_all = show_finished

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Try rich output; fall back to plain print
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich import box as _box

        con = Console()
        con.print(
            Panel(
                f"[bold bright_blue]Curie AI – Animated Dashboard[/bold bright_blue]\n\n"
                f"  [cyan]URL:[/cyan]  [underline]{url}[/underline]\n\n"
                f"  [dim]Curie and her sub-agents are visualized with live animations.\n"
                f"  Hover over a sub-agent card to see what it is doing.\n"
                f"  Press [bold]Ctrl-C[/bold] to stop the server.[/dim]",
                box=_box.ROUNDED,
                border_style="bright_blue",
                padding=(1, 3),
            )
        )
    except ImportError:
        print(f"\nCurie AI – Animated Dashboard")
        print(f"Open in your browser: {url}")
        print("Press Ctrl-C to stop.\n")

    # Open browser after a short delay so the server is ready
    def _open():
        time.sleep(0.4)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _SHUTDOWN_EVENT.set()
        server.shutdown()
        server.server_close()

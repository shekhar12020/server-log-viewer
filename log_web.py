#!/usr/bin/env python3
"""
Browser-based Docker log viewer (single file, standard library only).

Features:
- Lists running Docker containers and streams logs via Server-Sent Events (SSE)
- Optional sudo for Docker commands (LOG_WEB_DOCKER_SUDO=1)
- Optional bearer token for simple auth (LOG_WEB_TOKEN)
- Minimal HTML/JS UI with container dropdown, follow toggle, level/text filters

Run on EC2:
- python3 log_web.py  (defaults: 127.0.0.1:8080)
- To expose publicly: LOG_WEB_HOST=0.0.0.0 LOG_WEB_PORT=8080 python3 log_web.py
- If Docker requires sudo: LOG_WEB_DOCKER_SUDO=1 ...
- Add a token: LOG_WEB_TOKEN=your-secret ... (then use ?token=your-secret in URLs)

Security:
- Protect with a token and security group rules. Do not expose publicly without controls.
"""

import json
import os
import shlex
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


HOST = os.environ.get("LOG_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LOG_WEB_PORT", "8080"))
DOCKER_SUDO = os.environ.get("LOG_WEB_DOCKER_SUDO", "0").strip() in ("1", "true", "yes", "on")
DOCKER_BIN = os.environ.get("LOG_WEB_DOCKER_BIN", "docker").strip() or "docker"
TOKEN = os.environ.get("LOG_WEB_TOKEN", "")


def docker_cmd(args):
    if not args or args[0] != DOCKER_BIN:
        args = [DOCKER_BIN, *args]
    if DOCKER_SUDO:
        args = ["sudo", "-n", *args]
    return args


def _parse_containers(out: str):
    containers = []
    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 1 and parts[0]:
            name = parts[0]
            image = parts[1] if len(parts) > 1 else ""
            status = parts[2] if len(parts) > 2 else ""
            containers.append({"name": name, "image": image, "status": status})
    return containers

def list_containers():
    args = docker_cmd([DOCKER_BIN, "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=10)
        if proc.returncode != 0:
            return [], err.strip() or f"docker ps exited {proc.returncode}"
        containers = _parse_containers(out)
        if containers:
            return containers, None
        # Fallback to include stopped containers (some setups rely on ps -a)
        args2 = docker_cmd([DOCKER_BIN, "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])
        proc2 = subprocess.Popen(args2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out2, err2 = proc2.communicate(timeout=10)
        if proc2.returncode != 0:
            # Return the original empty with no error; UI will show empty list
            return [], None
        return _parse_containers(out2), None
    except Exception as e:
        return [], str(e)


INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QR Table Backend Logs</title>
  <style>
    * { box-sizing: border-box; }
    body { 
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
      margin: 0; 
      background: #f8f6f0;
      min-height: 100vh;
    }
    .container { 
      max-width: 1400px; 
      margin: 0 auto; 
      background: white; 
      border-radius: 12px; 
      box-shadow: 0 4px 20px rgba(0,0,0,0.08);
      overflow: hidden;
      margin: 10px auto;
      height: calc(100vh - 20px);
      border: 1px solid #e5e5e5;
      display: flex;
      flex-direction: column;
    }
    header { 
      background: #2563eb; 
      color: white; 
      padding: 20px 24px; 
      display: flex; 
      gap: 16px; 
      align-items: center; 
      flex-wrap: wrap;
      box-shadow: 0 2px 8px rgba(37, 99, 235, 0.15);
    }
    header h1 { 
      font-size: 24px; 
      margin: 0 16px 0 0; 
      font-weight: 700;
      text-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .controls { 
      display: flex; 
      gap: 12px; 
      align-items: center; 
      flex-wrap: wrap; 
      margin-left: auto;
    }
    .control-group { 
      display: flex; 
      flex-direction: column; 
      gap: 4px;
    }
    label { 
      font-size: 12px; 
      font-weight: 600; 
      text-transform: uppercase; 
      letter-spacing: 0.5px;
      opacity: 0.9;
    }
    select, input, button { 
      font-size: 14px; 
      padding: 10px 12px; 
      border: 1px solid #d1d5db; 
      border-radius: 6px; 
      background: white;
      color: #374151;
      font-weight: 500;
      transition: all 0.2s ease;
      box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    select:focus, input:focus { 
      outline: none; 
      border-color: #2563eb;
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
    }
    button { 
      background: #f97316; 
      color: white; 
      font-weight: 600; 
      cursor: pointer;
      border: none;
      font-size: 13px;
      width: 96px; /* fixed width for all buttons to prevent shifting */
      text-align: center;
    }
    button:hover { 
      background: #ea580c; 
      box-shadow: 0 2px 8px rgba(249, 115, 22, 0.3);
    }
    button:active { 
      background: #dc2626; 
    }
    .log-container {
      display: flex;
      flex: 1;
      overflow: hidden;
      /* Let it naturally fill remaining height for better responsiveness */
    }
    /* Prevent flex children from forcing horizontal overflow */
    .log-container > * { min-width: 0; }
    /* Responsive controls: wrap on smaller screens */
    @media (max-width: 1100px) {
      .controls { flex-wrap: wrap; gap: 8px; }
      .control-group { margin-right: 8px; }
      #containers { width: 100%; min-width: 260px; }
    }
    @media (max-width: 720px) {
      header { padding: 12px; }
      h1 { font-size: 18px; }
      .controls { flex-direction: column; align-items: stretch; }
      .control-group { width: 100%; margin-right: 0; }
      select, input, button { width: 100%; }
      .status-pill { width: 100%; }
      .log-container { height: calc(100vh - 260px); }
    }
    .line-numbers { 
      background: #161b22; 
      color: #7d8590; 
      padding: 0 10px 0 20px; 
      font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', 'Consolas', 'Monaco', monospace; 
      font-size: 13px; 
      line-height: 1.5; 
      text-align: right; 
      user-select: none; 
      border-right: 1px solid #30363d; 
      min-width: 50px; 
      overflow-y: auto; 
      overflow-x: hidden; /* prevent horizontal bleed on small screens */
      white-space: pre; 
      box-sizing: border-box; 
    }
    #log { 
      white-space: pre; 
      font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', 'Consolas', 'Monaco', monospace; 
      padding: 0 20px 0 20px; 
      flex: 1;
      overflow-y: auto; 
      overflow-x: auto; /* allow horizontal scroll on narrow screens */
      background: #0d1117; 
      color: #c9d1d9; 
      line-height: 1.5;
      border: 1px solid #30363d;
      font-size: 13px;
      border-radius: 0 0 12px 12px;
      box-sizing: border-box;
    }
    #log::-webkit-scrollbar { 
      width: 8px; 
    }
    #log::-webkit-scrollbar-track { 
      background: #161b22; 
    }
    #log::-webkit-scrollbar-thumb { 
      background: #30363d; 
      border-radius: 4px; 
    }
    #log::-webkit-scrollbar-thumb:hover { 
      background: #484f58; 
    }
    footer { 
      background: #f8fafc; 
      color: #64748b; 
      padding: 12px 24px; 
      font-size: 12px; 
      text-align: center;
      border-top: 1px solid #e2e8f0;
    }
    .status-pill { 
      background: #10b981; 
      color: white; 
      padding: 6px 12px; 
      border-radius: 16px; 
      font-weight: 600; 
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      box-shadow: 0 1px 3px rgba(16, 185, 129, 0.2);
      width: 72px; /* fixed width to prevent UI shifting */
      text-align: center;
      display: inline-block;
    }
    /* Container select: fluid by default, capped on desktop for stability */
    #containers {
      width: 100%;
      max-width: 520px;
      box-sizing: border-box;
    }
    #err { 
      color: #dc2626; 
      background: #fef2f2; 
      padding: 12px 16px; 
      margin: 16px 24px; 
      border: 1px solid #fecaca; 
      border-radius: 6px;
      display: none; 
      font-weight: 500;
      box-shadow: 0 1px 3px rgba(220, 38, 38, 0.1);
    }
    .log-line { 
      margin: 1px 0; 
      padding: 2px 0; 
      font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', 'Consolas', 'Monaco', monospace;
    }
    .log-line:hover { 
      background: rgba(255, 255, 255, 0.05); 
    }
    
    /* Log level color coding */
    .log-error { color: #f85149; }
    .log-warn { color: #d29922; }
    .log-info { color: #58a6ff; }
    .log-debug { color: #8b949e; }
    .log-success { color: #3fb950; }
    .log-timestamp { color: #7c3aed; opacity: 0.8; }
    .log-container { color: #f0f6fc; font-weight: 600; }
    @keyframes fadeIn { 
      from { opacity: 0; transform: translateY(10px); } 
      to { opacity: 1; transform: translateY(0); } 
    }
    .fade-in { 
      animation: fadeIn 0.3s ease; 
    }
    @media (max-width: 768px) { 
      .container { margin: 0; border-radius: 0; height: 100vh; }
      header { padding: 10px; flex-direction: column; align-items: stretch; gap: 8px; }
      /* Compact, two-column control grid to save vertical space */
      .controls { margin-left: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .control-group { width: 100%; margin-right: 0; }
      .control-group label { display: none; }
      /* Full-width inputs on the first rows */
      #containers, #level, #query { width: 100%; grid-column: 1 / -1; }
      /* Buttons in two columns */
      button { width: 100%; padding: 8px 10px; }
      .status-pill { width: 100%; grid-column: 1 / -1; }
      /* Maximize log area */
      .log-container { flex: 1; min-height: 60vh; }
      #log { padding: 10px; }
    }
  </style>
  <script>
      let es = null;
      let token = '';
      let isPaused = false; // Track pause state
      let scrollCheckEnabled = false; // Delay scroll checking until logs are flowing
      let userInteracting = false; // True briefly after wheel/touch/keys/mouse to mark manual intent
    
    function $(id){ return document.getElementById(id); }
        // Mark user interaction for a short window to disambiguate manual vs programmatic scroll
        setTimeout(() => {
          const logEl = $('log');
          if (!logEl) return;
          const markUser = () => { userInteracting = true; setTimeout(() => { userInteracting = false; }, 400); };
          ['wheel','touchstart','touchmove','mousedown'].forEach(ev => logEl.addEventListener(ev, markUser, { passive: true }));
          window.addEventListener('keydown', (e) => {
            if (['PageUp','PageDown','ArrowUp','ArrowDown','Home','End',' '].includes(e.key)) { markUser(); }
          });
        }, 0);
        let logText = '';
        let lineCount = 0;
        const MAX_LINES = 300; // Currently buffering 300 lines
        let updateTimeout = null;
        let autoScroll = true;
        let programmaticScroll = false; // guard to ignore our own scroll updates
        
        function addLine(text){
          // Don't add lines when paused
          if (isPaused) {
            return;
          }
          
          // Just append to text string - no array processing
          if (logText === '') {
            logText = text.trim();
          } else {
            logText += '\\n' + text.trim();
          }
          lineCount++;
          
          // Enable scroll checking after we have some logs (delay to prevent initial pause)
          if (lineCount === 10 && !scrollCheckEnabled) {
            setTimeout(() => {
              scrollCheckEnabled = true;
            }, 2000); // 2 second delay
          }
          
          // Clear any pending update
          if (updateTimeout) {
            clearTimeout(updateTimeout);
          }
          
          // Update display with a small delay to batch updates
          updateTimeout = setTimeout(() => {
            updateDisplay();
          }, 10);
        }
        
        function updateDisplay() {
          const log = $('log');
          
          // Simple line limit check
          if (lineCount > MAX_LINES) {
            const lines = logText.split('\\n');
            logText = lines.slice(-MAX_LINES).join('\\n');
            lineCount = MAX_LINES;
          }
          
          // Remove any leading newlines to eliminate white space at top
          let displayText = logText;
          while (displayText.startsWith('\\n')) {
            displayText = displayText.substring(1);
          }
          
          log.textContent = displayText;
          
          // Only auto-scroll if user hasn't manually scrolled up
          if (autoScroll) {
            programmaticScroll = true;
            log.scrollTop = log.scrollHeight;
            // release flag shortly after the scroll event fires
            setTimeout(() => { programmaticScroll = false; }, 0);
            // While auto scroll is active, force status to Live
            if (es) {
              const statusPill = document.querySelector('.status-pill');
              if (statusPill) { statusPill.textContent = 'Live'; statusPill.style.background = '#10b981'; }
            }
          }
        }
        
        function generateLineNumbers() {
          const lineNumbers = $('lineNumbers');
          
          // Generate static line numbers 0-299 to fix alignment
          let numbers = '';
          for (let i = 0; i < MAX_LINES; i++) {
            numbers += i.toString().padStart(3, ' ') + '\\n';
          }
          
          lineNumbers.textContent = numbers;
        }
        
        function syncScroll() {
          const log = $('log');
          const lineNumbers = $('lineNumbers');
          lineNumbers.scrollTop = log.scrollTop;
        }
        
        function setupScrollSync() {
          const log = $('log');
          const lineNumbers = $('lineNumbers');
          
          // Sync line numbers when log scrolls
          log.addEventListener('scroll', () => {
            lineNumbers.scrollTop = log.scrollTop;
          });
          
          // Sync log when line numbers scroll (if user clicks on line numbers)
          lineNumbers.addEventListener('scroll', () => {
            log.scrollTop = lineNumbers.scrollTop;
          });
        }
        
        function checkScrollPosition() {
          const log = $('log');
          if (programmaticScroll) return; // ignore scrolls caused by our own code
          
          // Update status indicator
          const statusPill = document.querySelector('.status-pill');
          if (!statusPill) return;
          
          if (!es) {
            // Not streaming: keep Ready
            statusPill.textContent = 'Ready';
            statusPill.style.background = '#3b82f6';
            return;
          }
          
          // Compute bottom state first with tight tolerance
          const tolerance = 2; // px
          const isAtBottom = (log.scrollHeight - log.clientHeight - log.scrollTop) <= tolerance;
          autoScroll = isAtBottom;
          
          console.log('Scroll check - isAtBottom:', isAtBottom, 'scrollCheckEnabled:', scrollCheckEnabled, 'isPaused:', isPaused);
          
          // If paused: resume immediately when at bottom; otherwise keep paused and exit
          if (isPaused) {
            if (isAtBottom) {
              isPaused = false;
              $('connect').textContent = '⏸ Pause';
              $('connect').style.background = '#f59e0b';
              statusPill.textContent = 'Live';
              statusPill.style.background = '#10b981';
            } else {
              statusPill.textContent = 'Paused';
              statusPill.style.background = '#f59e0b';
              return;
            }
          }
          
          // If streaming just started and no lines yet, keep Live
          if (es && lineCount === 0) {
            statusPill.textContent = 'Live';
            statusPill.style.background = '#10b981';
            return;
          }
          
          // Don't check scroll position until we have some logs and scroll checking is enabled
          if (!scrollCheckEnabled) {
            statusPill.textContent = 'Live';
            statusPill.style.background = '#10b981';
            // But still check if user scrolled to bottom to re-enable scroll checking
            if (isAtBottom) {
              console.log('Re-enabling scroll checking because user is at bottom');
              scrollCheckEnabled = true;
            }
            return;
          }
          
          // Sync line numbers scroll
          syncScroll();

          // Update status based on scroll position
          if (isAtBottom) {
            // At bottom - always Live and resume if paused
            console.log('At bottom - setting Live status');
            statusPill.textContent = 'Live';
            statusPill.style.background = '#10b981';
            if (isPaused) {
              console.log('Resuming from paused state');
              isPaused = false;
              $('connect').textContent = '⏸ Pause';
              $('connect').style.background = '#f59e0b';
            }
          } else {
            // Not at bottom - only pause if user is actually interacting
            if (userInteracting) {
              console.log('Not at bottom due to user interaction - setting Paused status');
              statusPill.textContent = 'Paused';
              statusPill.style.background = '#f59e0b';
              if (!isPaused) {
                console.log('Pausing from live state');
                isPaused = true;
                $('connect').textContent = '▶ Resume';
                $('connect').style.background = '#10b981';
              }
            } else {
              // Keep Live when not at bottom but no user interaction (layout shift)
              console.log('Not at bottom but no user interaction - keeping Live');
              statusPill.textContent = 'Live';
              statusPill.style.background = '#10b981';
            }
          }
        }
        
        
    function connect(){
      const c = $('containers');
      const name = c.value;
      const level = $('level').value;
      const q = $('query').value;
      
      // If already connected, toggle pause/resume
      if (es) {
        if (isPaused) {
          // Resume
          isPaused = false;
          $('connect').textContent = '⏸ Pause';
          $('connect').style.background = '#f59e0b';
          const statusPill = document.querySelector('.status-pill');
          if (statusPill) { statusPill.textContent = 'Live'; statusPill.style.background = '#10b981'; }
        } else {
          // Pause
          isPaused = true;
          $('connect').textContent = '▶ Resume';
          $('connect').style.background = '#10b981';
          const statusPill = document.querySelector('.status-pill');
          if (statusPill) { statusPill.textContent = 'Paused'; statusPill.style.background = '#f59e0b'; }
        }
        return;
      }
      
      // Start new connection
      if(!name){ 
        $('err').textContent = 'Please select a container first';
        $('err').style.display = 'block';
        setTimeout(() => $('err').style.display = 'none', 3000);
        return; 
      }
      
      const params = new URLSearchParams({ container: name, level: level, q: q });
      if(token){ params.set('token', token); }
      es = new EventSource('/stream?' + params.toString());
      logText = '';
        lineCount = 0;
        autoScroll = true;
        isPaused = false;
        scrollCheckEnabled = false; // Reset scroll checking
        const statusPill = document.querySelector('.status-pill');
        if (statusPill) { statusPill.textContent = 'Live'; statusPill.style.background = '#10b981'; }
      if (updateTimeout) clearTimeout(updateTimeout);
      
      // Update UI state
      $('connect').textContent = '⏸ Pause';
      $('connect').style.background = '#f59e0b';
      $('disconnect').disabled = false;
      $('disconnect').style.opacity = '1';
      $('containers').disabled = true;
      $('containers').style.opacity = '0.6';
      $('level').disabled = true;
      $('level').style.opacity = '0.6';
      $('query').disabled = true;
      $('query').style.opacity = '0.6';
      
      es.onmessage = (ev) => {
        try { const d = JSON.parse(ev.data); if(d.line){ addLine(d.line); } }
        catch(e){ addLine(ev.data); }
      };
      es.onerror = (e) => { 
        addLine('[error] stream disconnected'); 
        es && es.close(); 
        es = null;
        isPaused = false;
        resetUI();
        // Force status to Ready on error
        const statusPill = document.querySelector('.status-pill');
        if (statusPill) { statusPill.textContent = 'Ready'; statusPill.style.background = '#3b82f6'; }
      };
    }
    function disconnect(){ 
      if(es){ 
        es.close(); 
        es=null; 
        isPaused = false;
        addLine('[info] disconnected'); 
        resetUI();
        // Force status to Ready after manual stop
        const statusPill = document.querySelector('.status-pill');
        if (statusPill) { statusPill.textContent = 'Ready'; statusPill.style.background = '#3b82f6'; }
      } 
    }
    
    function resetUI() {
      isPaused = false;
      $('connect').textContent = '▶ Follow';
      $('connect').style.background = '#f97316';
      $('disconnect').disabled = true;
      $('disconnect').style.opacity = '0.5';
      $('containers').disabled = false;
      $('containers').style.opacity = '1';
      $('level').disabled = false;
      $('level').style.opacity = '1';
      $('query').disabled = false;
      $('query').style.opacity = '1';
      // Do not override status pill here; handled by stop/error
    }
        function clearLogs(){
          logText = '';
          lineCount = 0;
          if (updateTimeout) clearTimeout(updateTimeout);
          updateDisplay();
        }
        
    async function loadContainers(){
      const url = token ? ('/containers?token='+encodeURIComponent(token)) : '/containers';
      console.log('Loading containers from:', url);
      
      // Show loading state
      const c = $('containers');
      const originalText = c.innerHTML;
      // Keep width stable: use a non-empty placeholder with same length
      c.innerHTML = '<option disabled>Loading containers…</option>';
      c.disabled = true;
      
      const res = await fetch(url);
      c.innerHTML = '';
      $('err').style.display='none';
      
      if(!res.ok){
        let msg = 'Failed to load containers';
        try{ const j = await res.json(); if(j && j.error){ msg = j.error; } }catch(e){}
        console.error('API error:', msg);
        $('err').textContent = msg;
        $('err').style.display='block';
        c.innerHTML = originalText;
        c.disabled = false;
        return;
      }
      
      const data = await res.json();
      console.log('Loaded containers:', data.length);
      
      // Preserve previous selection
      const previousValue = c.value;

      // Add default option
      const defaultOpt = document.createElement('option');
      defaultOpt.value = '';
      defaultOpt.textContent = 'Select a container...';
      c.appendChild(defaultOpt);
      
      for(const it of data){
        const opt = document.createElement('option');
        opt.value = it.name; opt.textContent = it.name + (it.image?('  ['+it.image+']'):'');
        c.appendChild(opt);
      }
      
      c.disabled = false;

      // Try to restore previous selection if still present
      if (previousValue) {
        const hasPrev = Array.from(c.options).some(o => o.value === previousValue);
        if (hasPrev) { c.value = previousValue; }
      }
      
      if(!data.length){
        console.warn('No containers found');
        $('err').textContent = 'No containers found. Try enabling sudo or check Docker status.';
        $('err').style.display='block';
      } else {
        // After successful load, if not streaming keep status at Ready
        const statusPill = document.querySelector('.status-pill');
        if (statusPill) {
          if (es) {
            statusPill.textContent = 'Live';
            statusPill.style.background = '#10b981';
          } else {
            statusPill.textContent = 'Ready';
            statusPill.style.background = '#3b82f6';
          }
        }
      }
    }
    // Wrapper to reload container list: stops streaming, preserves scroll
    async function reloadContainers(ev){
      if (ev) ev.preventDefault();
      // Stop active follow first
      if (es) { try { es.close(); } catch(e){} es = null; }
      isPaused = false;
      // Reset UI buttons/states so Pause/Stop reflect stopped state
      resetUI();
      const statusPill = document.querySelector('.status-pill');
      if (statusPill) { statusPill.textContent = 'Ready'; statusPill.style.background = '#3b82f6'; }

      // Preserve scroll positions to avoid UI jumping
      const logEl = $('log');
      const lnEl = $('lineNumbers');
      const prevLogScroll = logEl ? logEl.scrollTop : 0;
      const prevLnScroll = lnEl ? lnEl.scrollTop : 0;

      await loadContainers();

      // Restore scroll positions
      if (logEl) logEl.scrollTop = prevLogScroll;
      if (lnEl) lnEl.scrollTop = prevLnScroll;
    }

    window.addEventListener('DOMContentLoaded', async () => {
      console.log('Page loaded, starting loadContainers...');
      const urlParams = new URLSearchParams(location.search);
      token = urlParams.get('token') || '';
      
      // Initialize UI state
      resetUI();
      
      // Generate static line numbers
      generateLineNumbers();
      
      // Setup scroll synchronization
      setupScrollSync();
      
      await loadContainers();
      $('connect').addEventListener('click', connect);
      $('disconnect').addEventListener('click', disconnect);
      $('reload').addEventListener('click', reloadContainers);
      $('clear').addEventListener('click', clearLogs);
      
      // Add scroll listener to detect manual scrolling
      $('log').addEventListener('scroll', checkScrollPosition);
    });
    
  </script>
  </head>
  <body>
    <div class="container">
      <header>
        <h1>📊 QR Table Backend Logs</h1>
        <div class="controls">
          <div class="control-group">
            <label>Container</label>
            <select id="containers">
              <option value="">Select a container...</option>
            </select>
          </div>
          <div class="control-group">
            <label>Level</label>
            <select id="level">
              <option>ANY</option><option>DEBUG</option><option>INFO</option><option>WARN</option><option>ERROR</option><option>CRITICAL</option>
            </select>
          </div>
          <div class="control-group">
            <label>Search</label>
            <input id="query" placeholder="Filter logs..." />
          </div>
          <div class="control-group">
            <label>&nbsp;</label>
            <button id="connect">▶ Follow</button>
          </div>
          <div class="control-group">
            <label>&nbsp;</label>
            <button id="disconnect">⏹ Stop</button>
          </div>
            <div class="control-group">
              <label>&nbsp;</label>
              <button id="reload" title="Refresh container list from Docker">🔄 Reload</button>
            </div>
          <div class="control-group">
            <label>&nbsp;</label>
            <button id="clear">🗑️ Clear</button>
          </div>
            <div class="control-group">
              <label>&nbsp;</label>
              <span class="status-pill">Live</span>
            </div>
        </div>
      </header>
      <div id="err"></div>
      <div class="log-container">
        <div class="line-numbers" id="lineNumbers"></div>
        <pre id="log"></pre>
      </div>
      <footer>💡 Tip: Add ?token=YOUR_TOKEN to the URL if the server requires authentication</footer>
    </div>
  </body>
</html>
"""


def send_json(handler: BaseHTTPRequestHandler, obj, status=HTTPStatus.OK):
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def check_token(query):
    if not TOKEN:
        return True
    return query.get("token", [""])[0] == TOKEN


def line_passes_filters(line: str, level: str, q: str) -> bool:
    if level and level.upper() != "ANY":
        # naive level check
        if level.upper() not in line.upper():
            return False
    if q and q.lower() not in line.lower():
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            html = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if path == "/containers":
            if not check_token(query):
                self.send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            items, err = list_containers()
            if err:
                send_json(self, {"error": err}, status=HTTPStatus.BAD_GATEWAY)
            else:
                send_json(self, items)
            return

        if path == "/stream":
            if not check_token(query):
                self.send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            name = query.get("container", [""])[0]
            level = query.get("level", ["ANY"])[0]
            q = query.get("q", [""])[0]
            if not name:
                self.send_error(HTTPStatus.BAD_REQUEST, "container param required")
                return
            cmd = docker_cmd(["docker", "logs", "-f", "--since", "0s", "--tail", "500", name])
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            except Exception as e:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"failed to start docker logs: {e}")
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def write_event(obj):
                try:
                    data = json.dumps(obj, ensure_ascii=False)
                    payload = f"data: {data}\n\n".encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
                except Exception:
                    pass

            write_event({"line": f"[info] following {name}"})
            try:
                stdout = proc.stdout
                stderr = proc.stderr
                last_flush = time.time()
                while True:
                    if stdout is None:
                        break
                    line = stdout.readline()
                    if line:
                        line = line.rstrip("\n")
                        if line_passes_filters(line, level, q):
                            write_event({"line": line})
                    else:
                        if proc.poll() is not None:
                            break
                        # occasional flush
                        if time.time() - last_flush > 10:
                            write_event({"ping": int(time.time())})
                            last_flush = time.time()
                        time.sleep(0.1)

                # If any error output remains
                if stderr is not None:
                    err_tail = stderr.read() or ""
                    if err_tail.strip():
                        write_event({"line": f"[error] {err_tail.strip()}"})
            finally:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, fmt, *args):  # quieter server logs
        return


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving on http://{HOST}:{PORT}  (sudo={'ON' if DOCKER_SUDO else 'OFF'})")
    if TOKEN:
        print("Auth token required. Provide ?token=<token> in requests.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()



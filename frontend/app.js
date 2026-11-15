/* ═══════════════════════════════════════════════════════════════════════════
   HNIDS Dashboard — Client Application
   Connects to the FastAPI backend on the same host
   ═══════════════════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ─── Configuration ───
  const API_BASE = window.location.port === '3000'
    ? 'http://127.0.0.1:8000'
    : `${window.location.protocol}//${window.location.hostname}:8000`;

  const CLASSES = ['BENIGN', 'DoS', 'DDoS', 'PortScan', 'BruteForce', 'WebAttack', 'Bot', 'Rare'];
  const CLASS_COLOURS = {
    BENIGN:     '#4ade80',
    DoS:        '#ff6b4a',
    DDoS:       '#ff4d4d',
    PortScan:   '#ffb020',
    BruteForce: '#f84464',
    WebAttack:  '#ea4335',
    Bot:        '#4cc9f0',
    Rare:       '#d4c5f9',
  };

  // ─── State ───
  const state = {
    detections: [],
    classCounts: Object.fromEntries(CLASSES.map(c => [c, 0])),
    totalRequests: 0,
    totalAlerts: 0,
    confidenceSum: 0,
    confidenceCount: 0,
    bufferCount: 0,
    windowLength: 20,
  };

  // ─── DOM References ───
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    statusDot:       $('#status-dot'),
    statusLabel:     $('#status-label'),
    bundleVersion:   $('#bundle-version'),
    totalRequests:   $('#total-requests'),
    totalAlerts:     $('#total-alerts'),
    avgConfidence:   $('#avg-confidence'),
    posterWall:      $('#poster-wall'),
    detectionFeed:   $('#detection-feed'),
    detectionCount:  $('#detection-count'),
    detectInput:     $('#detect-input'),
    apiKeyDetect:    $('#api-key-detect'),
    btnDetect:       $('#btn-detect'),
    btnLoadExample:  $('#btn-load-example'),
    resultPlaceholder: $('#result-placeholder'),
    resultContent:   $('#result-content'),
    resultClass:     $('#result-class'),
    resultConfidence: $('#result-confidence'),
    resultDetails:   $('#result-details'),
    resultProbs:     $('#result-probs'),
    streamInput:     $('#stream-input'),
    apiKeyStream:    $('#api-key-stream'),
    btnStream:       $('#btn-stream'),
    bufferFill:      $('#buffer-fill'),
    bufferLabel:     $('#buffer-label'),
    bufferInfo:      $('#buffer-info'),
    streamLog:       $('#stream-log'),
    btnRefreshMetrics: $('#btn-refresh-metrics'),
    metricsContent:  $('#metrics-content'),
    alertBanner:     $('#alert-banner'),
    alertBannerText: $('#alert-banner-text'),
    alertBannerClose: $('#alert-banner-close'),
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  NAVIGATION
  // ═══════════════════════════════════════════════════════════════════════════
  function initNav() {
    const navBtns = $$('.nav-btn');
    navBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        navBtns.forEach(b => {
          b.classList.toggle('active', b === btn);
          b.removeAttribute('aria-current');
        });
        btn.setAttribute('aria-current', 'page');
        $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${view}`));
        if (view === 'metrics') loadMetrics();
      });
    });

    // Keyboard shortcuts (1-4)
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const idx = parseInt(e.key) - 1;
      if (idx >= 0 && idx < navBtns.length) {
        navBtns[idx].click();
      }
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  POSTER WALL — Detection class panels
  // ═══════════════════════════════════════════════════════════════════════════
  function buildPosterWall() {
    dom.posterWall.innerHTML = CLASSES.map(cls => `
      <div class="poster" data-class="${cls}">
        <span class="poster__label">${cls}</span>
        <span class="poster__count" style="color: ${CLASS_COLOURS[cls]}" id="poster-${cls}">0</span>
        <div class="poster__bar">
          <div class="poster__bar-fill" id="poster-bar-${cls}" style="width: 0%; background: ${CLASS_COLOURS[cls]}"></div>
        </div>
      </div>
    `).join('');
  }

  function updatePosterWall() {
    const total = Math.max(1, Object.values(state.classCounts).reduce((a, b) => a + b, 0));
    CLASSES.forEach(cls => {
      const count = state.classCounts[cls];
      const pct = (count / total * 100).toFixed(1);
      const countEl = $(`#poster-${cls}`);
      const barEl = $(`#poster-bar-${cls}`);
      if (countEl) countEl.textContent = count;
      if (barEl) barEl.style.width = `${pct}%`;
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  HEALTH CHECK
  // ═══════════════════════════════════════════════════════════════════════════
  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/readyz`);
      const data = await res.json();
      if (data.ready) {
        dom.statusDot.className = 'status-dot status-dot--ok';
        dom.statusLabel.textContent = 'READY';
        dom.statusLabel.style.color = '#4ade80';
        dom.bundleVersion.textContent = data.bundle_version || '—';
        return true;
      } else {
        dom.statusDot.className = 'status-dot status-dot--bad';
        dom.statusLabel.textContent = 'NOT READY';
        dom.statusLabel.style.color = '#ff4d4d';
        return false;
      }
    } catch {
      dom.statusDot.className = 'status-dot status-dot--bad';
      dom.statusLabel.textContent = 'OFFLINE';
      dom.statusLabel.style.color = '#ff4d4d';
      return false;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  API CALLS
  // ═══════════════════════════════════════════════════════════════════════════
  async function apiPost(endpoint, body, apiKey) {
    const headers = { 'Content-Type': 'application/json' };
    if (apiKey) headers['X-Api-Key'] = apiKey;
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
      throw new Error(err.error?.message || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  DETECT
  // ═══════════════════════════════════════════════════════════════════════════
  async function runDetect() {
    const raw = dom.detectInput.value.trim();
    if (!raw) return showAlert('PASTE A JSON PAYLOAD FIRST');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showAlert('INVALID JSON — CHECK YOUR PAYLOAD');
    }

    dom.btnDetect.disabled = true;
    dom.btnDetect.textContent = 'DETECTING…';

    try {
      const result = await apiPost('/v1/detect', payload, dom.apiKeyDetect.value);
      state.totalRequests++;
      recordDetection(result);
      showResult(result);
    } catch (err) {
      showAlert(`DETECTION FAILED — ${err.message}`);
    } finally {
      dom.btnDetect.disabled = false;
      dom.btnDetect.innerHTML = '<span class="btn__key" aria-hidden="true">⏎</span> DETECT';
    }
  }

  function showResult(event) {
    dom.resultPlaceholder.classList.add('hidden');
    dom.resultContent.classList.remove('hidden');

    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const isAlert = event.hnids.is_alert;

    dom.resultClass.textContent = cls;
    dom.resultClass.style.color = CLASS_COLOURS[cls] || '#f3efe6';

    dom.resultConfidence.textContent = `${(conf * 100).toFixed(2)}% CONFIDENCE`;
    dom.resultConfidence.style.color = conf >= 0.9 ? '#4ade80' : conf >= 0.6 ? '#ff9f1c' : '#ff4d4d';

    // Details grid
    dom.resultDetails.innerHTML = [
      ['KIND', event.event.kind.toUpperCase()],
      ['ALERT', isAlert ? 'YES' : 'NO'],
      ['BUNDLE', event.hnids.bundle_version],
      ['VALIDATION', event.hnids.validation_status.toUpperCase()],
      ['SANITIZED', event.hnids.sanitized_values],
      ['DETECTION ID', event.detection_id.slice(0, 8) + '…'],
    ].map(([k, v]) =>
      `<span class="result-details__key">${k}</span><span class="result-details__val">${v}</span>`
    ).join('');

    // Probability bars
    const probs = event.hnids.probabilities;
    dom.resultProbs.innerHTML = Object.entries(probs)
      .sort((a, b) => b[1] - a[1])
      .map(([name, prob]) => {
        const pct = (prob * 100).toFixed(2);
        return `
          <div class="prob-row">
            <span class="prob-row__label">${name}</span>
            <div class="prob-row__bar">
              <div class="prob-row__bar-fill" style="width: ${pct}%; background: ${CLASS_COLOURS[name] || '#a3a09a'}"></div>
            </div>
            <span class="prob-row__val" style="color: ${CLASS_COLOURS[name] || '#a3a09a'}">${pct}%</span>
          </div>
        `;
      }).join('');

    // Re-trigger animation
    dom.resultContent.style.animation = 'none';
    dom.resultContent.offsetHeight; // reflow
    dom.resultContent.style.animation = '';
  }

  // ─── Load Example ───
  async function loadExample() {
    try {
      const examples = [
        'detect_ddos.request.json',
        'detect_dos.request.json',
        'detect_benign.request.json',
        'detect_portscan.request.json',
        'detect_webattack.request.json',
      ];
      const pick = examples[Math.floor(Math.random() * examples.length)];
      const res = await fetch(`/examples/${pick}`);
      if (res.ok) {
        const data = await res.json();
        dom.detectInput.value = JSON.stringify(data, null, 2);
      } else {
        // Fallback: load from API base
        const res2 = await fetch(`${API_BASE}/../examples/${pick}`);
        if (res2.ok) {
          dom.detectInput.value = JSON.stringify(await res2.json(), null, 2);
        } else {
          // Use a hardcoded minimal example
          dom.detectInput.value = JSON.stringify(EXAMPLE_PAYLOAD, null, 2);
        }
      }
    } catch {
      dom.detectInput.value = JSON.stringify(EXAMPLE_PAYLOAD, null, 2);
    }
  }

  // Minimal example payload
  const EXAMPLE_PAYLOAD = {
    "flows": Array.from({ length: 20 }, (_, i) => ({
      "session_id": "demo-session-001",
      "timestamp": new Date(Date.now() + i * 1000).toISOString(),
      "features": {
        "protocol": 6, "flow_duration": 100000 + i * 1000,
        "total_fwd_packets": 10 + i, "total_backward_packets": 8 + i,
        "total_length_of_fwd_packets": 1500 + i * 100,
        "total_length_of_bwd_packets": 1200 + i * 80,
        "fwd_packet_length_max": 1460, "fwd_packet_length_min": 40,
        "fwd_packet_length_mean": 500, "fwd_packet_length_std": 200,
        "bwd_packet_length_max": 1460, "bwd_packet_length_min": 40,
        "bwd_packet_length_mean": 400, "bwd_packet_length_std": 180,
        "flow_bytes_per_s": 50000, "flow_packets_per_s": 100,
        "flow_iat_mean": 10000, "flow_iat_std": 5000,
        "flow_iat_max": 20000, "flow_iat_min": 1000,
        "fwd_iat_total": 50000, "fwd_iat_mean": 5000,
        "fwd_iat_std": 2500, "fwd_iat_max": 10000, "fwd_iat_min": 500,
        "bwd_iat_total": 40000, "bwd_iat_mean": 4000,
        "bwd_iat_std": 2000, "bwd_iat_max": 8000, "bwd_iat_min": 400,
        "fwd_psh_flags": 1, "bwd_psh_flags": 0,
        "fwd_urg_flags": 0, "bwd_urg_flags": 0,
        "fwd_header_length": 320, "bwd_header_length": 280,
        "fwd_packets_per_s": 50, "bwd_packets_per_s": 40,
        "min_packet_length": 40, "max_packet_length": 1460,
        "packet_length_mean": 450, "packet_length_std": 300,
        "packet_length_variance": 90000,
        "fin_flag_count": 0, "syn_flag_count": 1,
        "rst_flag_count": 0, "psh_flag_count": 1,
        "ack_flag_count": 1, "urg_flag_count": 0,
        "cwe_flag_count": 0, "ece_flag_count": 0,
        "down_up_ratio": 0.8,
        "average_packet_size": 450,
        "avg_fwd_segment_size": 500, "avg_bwd_segment_size": 400,
        "fwd_avg_bytes_per_bulk": 0, "fwd_avg_packets_per_bulk": 0,
        "fwd_avg_bulk_rate": 0,
        "bwd_avg_bytes_per_bulk": 0, "bwd_avg_packets_per_bulk": 0,
        "bwd_avg_bulk_rate": 0,
        "subflow_fwd_packets": 10, "subflow_fwd_bytes": 1500,
        "subflow_bwd_packets": 8, "subflow_bwd_bytes": 1200,
        "init_win_bytes_forward": 65535, "init_win_bytes_backward": 65535,
        "act_data_pkt_fwd": 8, "min_seg_size_forward": 40,
        "active_mean": 50000, "active_std": 10000,
        "active_max": 80000, "active_min": 20000,
        "idle_mean": 100000, "idle_std": 50000,
        "idle_max": 200000, "idle_min": 50000,
        "rate_nonfinite": 0
      }
    }))
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  STREAM
  // ═══════════════════════════════════════════════════════════════════════════
  async function pushFlow() {
    const raw = dom.streamInput.value.trim();
    if (!raw) return showAlert('PASTE A FLOW RECORD FIRST');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showAlert('INVALID JSON — CHECK YOUR FLOW RECORD');
    }

    dom.btnStream.disabled = true;

    try {
      const result = await apiPost('/v1/flows', payload, dom.apiKeyStream.value);
      state.bufferCount = result.buffered;
      state.windowLength = result.window_length;
      updateBuffer();

      const time = new Date().toLocaleTimeString();
      let logHtml = '';

      if (result.detection) {
        recordDetection(result.detection);
        logHtml = `
          <div class="stream-entry stream-entry--alert">
            <span class="stream-entry__time">${time}</span>
            <span class="stream-entry__text">⚠ DETECTION: ${result.detection.hnids.predicted_class} (${(result.detection.hnids.confidence * 100).toFixed(1)}%)</span>
          </div>
        `;
        state.bufferCount = 0;
        updateBuffer();
      } else {
        logHtml = `
          <div class="stream-entry">
            <span class="stream-entry__time">${time}</span>
            <span class="stream-entry__text">Buffered: ${result.buffered} / ${result.window_length}</span>
          </div>
        `;
      }

      const logEl = dom.streamLog;
      const isEmpty = logEl.querySelector('.empty-state');
      if (isEmpty) isEmpty.remove();
      logEl.insertAdjacentHTML('afterbegin', logHtml);

    } catch (err) {
      showAlert(`STREAM FAILED — ${err.message}`);
    } finally {
      dom.btnStream.disabled = false;
    }
  }

  function updateBuffer() {
    const pct = Math.min(100, (state.bufferCount / state.windowLength) * 100);
    dom.bufferFill.style.width = `${pct}%`;
    dom.bufferLabel.textContent = `${state.bufferCount} / ${state.windowLength}`;

    if (state.bufferCount === 0) {
      dom.bufferFill.style.background = 'var(--blue)';
      dom.bufferInfo.innerHTML = '<p class="buffer-info__text">Buffer reset. Push more flows.</p>';
    } else if (pct >= 80) {
      dom.bufferFill.style.background = 'var(--warn)';
      dom.bufferInfo.innerHTML = '<p class="buffer-info__text" style="color: var(--warn)">Almost full — detection will fire soon.</p>';
    } else {
      dom.bufferFill.style.background = 'var(--blue)';
      dom.bufferInfo.innerHTML = `<p class="buffer-info__text">${state.windowLength - state.bufferCount} more flows needed.</p>`;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  DETECTION TRACKING
  // ═══════════════════════════════════════════════════════════════════════════
  function recordDetection(event) {
    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const isAlert = event.hnids.is_alert;

    state.detections.unshift(event);
    if (state.detections.length > 100) state.detections.pop();

    state.classCounts[cls] = (state.classCounts[cls] || 0) + 1;
    if (isAlert) state.totalAlerts++;
    state.confidenceSum += conf;
    state.confidenceCount++;

    updateDashboard();
    addFeedEntry(event);

    if (isAlert && cls !== 'BENIGN') {
      showAlert(`${cls} DETECTED — ${(conf * 100).toFixed(1)}% CONFIDENCE`);
    }
  }

  function updateDashboard() {
    dom.totalRequests.textContent = state.totalRequests;
    dom.totalAlerts.textContent = state.totalAlerts;

    if (state.confidenceCount > 0) {
      const avg = (state.confidenceSum / state.confidenceCount * 100).toFixed(1);
      dom.avgConfidence.textContent = `${avg}%`;
    }

    dom.detectionCount.textContent = state.detections.length;
    updatePosterWall();
  }

  function addFeedEntry(event) {
    const feed = dom.detectionFeed;
    const empty = feed.querySelector('.empty-state');
    if (empty) empty.remove();

    const cls = event.hnids.predicted_class;
    const conf = (event.hnids.confidence * 100).toFixed(1);
    const kind = event.event.kind;
    const time = new Date(event.event.created).toLocaleTimeString();

    const entry = document.createElement('div');
    entry.className = 'detection-entry';
    entry.innerHTML = `
      <span class="detection-entry__kind detection-entry__kind--${kind}">${kind}</span>
      <span class="detection-entry__class" style="color: ${CLASS_COLOURS[cls] || '#f3efe6'}">${cls}</span>
      <span class="detection-entry__confidence">${conf}%</span>
      <span class="detection-entry__time">${time}</span>
    `;

    feed.insertBefore(entry, feed.firstChild);

    // Keep max 50 entries
    while (feed.children.length > 50) {
      feed.removeChild(feed.lastChild);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  METRICS
  // ═══════════════════════════════════════════════════════════════════════════
  async function loadMetrics() {
    try {
      const res = await fetch(`${API_BASE}/metrics`);
      if (res.ok) {
        const text = await res.text();
        dom.metricsContent.textContent = text || 'No metrics available yet.';
      } else {
        dom.metricsContent.textContent = `Error loading metrics: HTTP ${res.status}`;
      }
    } catch {
      dom.metricsContent.textContent = 'Cannot reach metrics endpoint. Is the server running?';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  ALERT BANNER
  // ═══════════════════════════════════════════════════════════════════════════
  let alertTimeout = null;

  function showAlert(message) {
    dom.alertBannerText.textContent = message;
    dom.alertBanner.classList.remove('hidden');
    dom.alertBanner.style.animation = 'none';
    dom.alertBanner.offsetHeight;
    dom.alertBanner.style.animation = '';

    clearTimeout(alertTimeout);
    alertTimeout = setTimeout(() => {
      dom.alertBanner.classList.add('hidden');
    }, 5000);
  }

  function hideAlert() {
    dom.alertBanner.classList.add('hidden');
    clearTimeout(alertTimeout);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  INIT
  // ═══════════════════════════════════════════════════════════════════════════
  function init() {
    initNav();
    buildPosterWall();
    updateDashboard();

    // Event listeners
    dom.btnDetect.addEventListener('click', runDetect);
    dom.btnLoadExample.addEventListener('click', loadExample);
    dom.btnStream.addEventListener('click', pushFlow);
    dom.btnRefreshMetrics.addEventListener('click', loadMetrics);
    dom.alertBannerClose.addEventListener('click', hideAlert);

    // Enter key in textareas triggers detect/stream
    dom.detectInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.metaKey) runDetect();
    });
    dom.streamInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.metaKey) pushFlow();
    });

    // Initial health check
    checkHealth();

    // Periodic health check every 15s
    setInterval(checkHealth, 15000);
  }

  // Wait for DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

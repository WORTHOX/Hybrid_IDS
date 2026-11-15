/* ═══════════════════════════════════════════════════════════════════════════
   AEGIS CYBER DEFENSE — TELEMETRY ENGINE
   Direct Proxy Routing: /api/* → FastAPI Engine on port 8000
   ═══════════════════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ─── Configuration ───
  const API_BASE = '/api';

  const CLASSES = [
    'BENIGN',
    'DoS',
    'DDoS',
    'PortScan',
    'BruteForce',
    'WebAttack',
    'Bot',
    'Rare'
  ];

  // Punchy Overclock Palette
  const CLASS_COLORS = {
    BENIGN:     '#4ADE80', // Matrix Green
    DoS:        '#FFB020', // Boost Gold
    DDoS:       '#FF4D4D', // Hot Crimson
    PortScan:   '#FBBC04', // Laser Yellow
    BruteForce: '#F84464', // Hot Magenta
    WebAttack:  '#FF6B4A', // Hot Coral
    Bot:        '#4CC9F0', // Electric Sky Blue
    Rare:       '#D4C5F9', // Neon Lilac
  };

  const CLASS_SHORT_CODES = {
    BENIGN:     '01',
    DoS:        '02',
    DDoS:       '03',
    PortScan:   '04',
    BruteForce: '05',
    WebAttack:  '06',
    Bot:        '07',
    Rare:       '08',
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
    statusMark:        $('#status-mark'),
    statusText:        $('#status-text'),
    bundleVersion:     $('#bundle-version'),
    totalRequests:     $('#total-requests'),
    totalAlerts:       $('#total-alerts'),
    avgConfidence:     $('#avg-confidence'),
    distribution:      $('#distribution'),
    detectionFeed:     $('#detection-feed'),
    detectionCount:    $('#detection-count'),
    detectInput:       $('#detect-input'),
    apiKeyDetect:      $('#api-key-detect'),
    btnDetect:         $('#btn-detect'),
    btnLoadExample:    $('#btn-load-example'),
    resultPlaceholder: $('#result-placeholder'),
    resultContent:     $('#result-content'),
    resultClass:       $('#result-class'),
    resultConfidence:  $('#result-confidence'),
    verdictTag:        $('#verdict-tag'),
    verdictBanner:     $('#verdict-banner-wrap'),
    resultDetails:     $('#result-details'),
    resultProbs:       $('#result-probs'),
    streamInput:       $('#stream-input'),
    apiKeyStream:      $('#api-key-stream'),
    btnStream:         $('#btn-stream'),
    bufferFill:        $('#buffer-fill'),
    bufferLabel:       $('#buffer-label'),
    streamLog:         $('#stream-log'),
    btnRefreshMetrics: $('#btn-refresh-metrics'),
    metricsContent:    $('#metrics-content'),
    toast:             $('#toast'),
    toastText:         $('#toast-text'),
    toastClose:        $('#toast-close'),
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  1. NAVIGATION & HOTKEYS [1-4]
  // ═══════════════════════════════════════════════════════════════════════════
  function initNav() {
    const navBtns = $$('.nav-btn');
    navBtns.forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const view = btn.dataset.view;
        switchTab(view);
      });
    });

    // Keyboard Hotkeys 1-4 for instant switching
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const idx = parseInt(e.key, 10) - 1;
      if (idx >= 0 && idx < navBtns.length) {
        navBtns[idx].click();
      }
    });
  }

  function switchTab(viewName) {
    const navBtns = $$('.nav-btn');
    navBtns.forEach(b => {
      const isMatch = b.dataset.view === viewName;
      b.classList.toggle('active', isMatch);
      b.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    $$('.view').forEach(v => {
      const isMatch = v.id === `view-${viewName}`;
      v.classList.toggle('active', isMatch);
    });

    if (viewName === 'metrics') {
      loadMetrics();
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  2. 8-COLUMN THREAT CLASSIFICATION MATRIX
  // ═══════════════════════════════════════════════════════════════════════════
  function buildMatrix() {
    if (!dom.distribution) return;
    dom.distribution.innerHTML = CLASSES.map(cls => `
      <div class="matrix-tile" data-class="${cls}">
        <div class="matrix-tile__top">
          <span class="matrix-tile__idx">${CLASS_SHORT_CODES[cls]}</span>
          <span class="matrix-tile__pill" style="color: ${CLASS_COLORS[cls]}; background: ${CLASS_COLORS[cls]}1A;">ACTIVE</span>
        </div>
        <div class="matrix-tile__name" title="${cls}">${cls}</div>
        <div class="matrix-tile__num" id="matrix-num-${cls}">0</div>
        <div class="matrix-tile__track">
          <div class="matrix-tile__fill" id="matrix-bar-${cls}" style="width: 0%; background-color: ${CLASS_COLORS[cls]};"></div>
        </div>
        <div class="matrix-tile__pct" id="matrix-pct-${cls}">0.0%</div>
      </div>
    `).join('');
  }

  function updateMatrix() {
    const total = Math.max(1, Object.values(state.classCounts).reduce((a, b) => a + b, 0));
    CLASSES.forEach(cls => {
      const count = state.classCounts[cls];
      const pct = (count / total * 100).toFixed(1);
      const numEl = $(`#matrix-num-${cls}`);
      const barEl = $(`#matrix-bar-${cls}`);
      const pctEl = $(`#matrix-pct-${cls}`);

      if (numEl) numEl.textContent = count;
      if (barEl) barEl.style.width = `${pct}%`;
      if (pctEl) pctEl.textContent = `${pct}%`;
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  3. SENSOR HEALTH & MODEL STATUS
  // ═══════════════════════════════════════════════════════════════════════════
  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/readyz`);
      const data = await res.json();
      if (data.ready) {
        if (dom.statusMark) dom.statusMark.className = 'sensor-beacon ok';
        if (dom.statusText) dom.statusText.textContent = 'RADAR READY';
        if (dom.bundleVersion) dom.bundleVersion.textContent = data.bundle_version || 'b20261111T112951Z';
        return true;
      } else {
        if (dom.statusMark) dom.statusMark.className = 'sensor-beacon bad';
        if (dom.statusText) dom.statusText.textContent = 'SENSOR FAULT';
        return false;
      }
    } catch {
      if (dom.statusMark) dom.statusMark.className = 'sensor-beacon bad';
      if (dom.statusText) dom.statusText.textContent = 'OFFLINE';
      return false;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  4. API ENGINE DISPATCH
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
  //  5. FLOW DETECTION TEST BENCH
  // ═══════════════════════════════════════════════════════════════════════════
  async function runDetect() {
    const raw = dom.detectInput.value.trim();
    if (!raw) return showToast('Please provide a 20-flow JSON payload.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Syntax error: invalid JSON payload.');
    }

    dom.btnDetect.disabled = true;
    dom.btnDetect.innerHTML = '<span class="btn-keycap">…</span><span>INFERRING FLOWS…</span>';

    try {
      const result = await apiPost('/v1/detect', payload, dom.apiKeyDetect.value);
      state.totalRequests++;
      recordDetection(result);
      showResult(result);
    } catch (err) {
      showToast(`Evaluation Failed: ${err.message}`);
    } finally {
      dom.btnDetect.disabled = false;
      dom.btnDetect.innerHTML = '<span class="btn-keycap" aria-hidden="true">⏎</span><span>EXECUTE CLASSIFICATION</span>';
    }
  }

  function showResult(event) {
    if (!dom.resultPlaceholder || !dom.resultContent) return;
    dom.resultPlaceholder.classList.add('hidden');
    dom.resultContent.classList.remove('hidden');

    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const isAlert = event.hnids.is_alert;
    const clsColor = CLASS_COLORS[cls] || '#F1F5F9';

    dom.resultClass.textContent = cls;
    dom.resultClass.style.color = clsColor;

    if (dom.verdictTag) {
      dom.verdictTag.textContent = isAlert ? 'THREAT DETECTED' : 'CLEAN FLOW';
      dom.verdictTag.style.color = isAlert ? 'var(--cyber-crimson)' : 'var(--cyber-emerald)';
      dom.verdictTag.style.backgroundColor = isAlert ? 'var(--cyber-crimson-bg)' : 'var(--cyber-emerald-bg)';
    }

    dom.resultConfidence.textContent = `${(conf * 100).toFixed(2)}% CONFIDENCE`;
    dom.resultConfidence.style.color = conf >= 0.9 ? 'var(--cyber-emerald)' : conf >= 0.6 ? 'var(--cyber-amber)' : 'var(--cyber-crimson)';

    // Key-value metadata table
    dom.resultDetails.innerHTML = [
      ['CLASSIFICATION KIND', event.event.kind.toUpperCase()],
      ['ALERT TRIGGER', isAlert ? 'YES (ACTIONABLE)' : 'NO (BENIGN)'],
      ['MODEL BUNDLE', event.hnids.bundle_version],
      ['VALIDATION STATUS', event.hnids.validation_status.toUpperCase()],
      ['SANITIZED VALUES', event.hnids.sanitized_values],
      ['DETECTION ID', event.detection_id.slice(0, 16)],
    ].map(([k, v]) => `
      <div class="meta-field">
        <span class="meta-field__k">${k}</span>
        <span class="meta-field__v">${v}</span>
      </div>
    `).join('');

    // Probability breakdown spectrum
    const probs = event.hnids.probabilities || {};
    dom.resultProbs.innerHTML = Object.entries(probs)
      .sort((a, b) => b[1] - a[1])
      .map(([name, prob]) => {
        const pct = (prob * 100).toFixed(2);
        const barColor = CLASS_COLORS[name] || 'var(--text-muted)';
        return `
          <div class="prob-row-item">
            <span class="prob-name">${name}</span>
            <div class="prob-track">
              <div class="prob-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
            </div>
            <span class="prob-val">${pct}%</span>
          </div>
        `;
      }).join('');
  }

  // Preset sample loader
  async function loadPresetSample(sampleKey) {
    const filenameMap = {
      ddos:      'detect_ddos.request.json',
      dos:       'detect_dos.request.json',
      benign:    'detect_benign.request.json',
      portscan:  'detect_portscan.request.json',
      webattack: 'detect_webattack.request.json',
    };

    const targetFile = filenameMap[sampleKey] || 'detect_ddos.request.json';

    try {
      const res = await fetch(`/examples/${targetFile}`);
      if (res.ok) {
        const data = await res.json();
        dom.detectInput.value = JSON.stringify(data, null, 2);
        showToast(`Loaded ${sampleKey.toUpperCase()} network attack sample.`);
      } else {
        dom.detectInput.value = JSON.stringify(FALLBACK_PAYLOAD, null, 2);
      }
    } catch {
      dom.detectInput.value = JSON.stringify(FALLBACK_PAYLOAD, null, 2);
    }
  }

  function initPresetChips() {
    const chips = $$('.btn-preset');
    chips.forEach(chip => {
      chip.addEventListener('click', () => {
        const sample = chip.dataset.sample;
        loadPresetSample(sample);
      });
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  6. PACKET STREAM ACCUMULATOR
  // ═══════════════════════════════════════════════════════════════════════════
  async function pushFlow() {
    const raw = dom.streamInput.value.trim();
    if (!raw) return showToast('Please enter single flow JSON packet.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Malformed JSON flow object.');
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
        const cls = result.detection.hnids.predicted_class;
        const conf = (result.detection.hnids.confidence * 100).toFixed(1);
        logHtml = `
          <div class="stream-log-entry stream-log-entry--alert">
            <span class="log-time">${time}</span>
            <span class="log-msg">⚡ <strong>ACCUMULATED 20 FLOWS: INFERENCE FIRED</strong> → <strong>${cls}</strong> (${conf}% confidence)</span>
          </div>
        `;
        state.bufferCount = 0;
        updateBuffer();
      } else {
        logHtml = `
          <div class="stream-log-entry">
            <span class="log-time">${time}</span>
            <span class="log-msg">Flow received and queued [Buffer: ${result.buffered} / ${result.window_length}]</span>
          </div>
        `;
      }

      const logEl = dom.streamLog;
      const emptyEl = logEl.querySelector('.feed-empty-state');
      if (emptyEl) emptyEl.remove();
      logEl.insertAdjacentHTML('afterbegin', logHtml);

    } catch (err) {
      showToast(`Stream push failed: ${err.message}`);
    } finally {
      dom.btnStream.disabled = false;
    }
  }

  function updateBuffer() {
    const pct = Math.min(100, (state.bufferCount / state.windowLength) * 100);
    if (dom.bufferFill) {
      dom.bufferFill.style.width = `${pct}%`;
      dom.bufferFill.style.backgroundColor = pct >= 80 ? 'var(--cyber-crimson)' : 'var(--cyber-cyan)';
    }
    if (dom.bufferLabel) {
      dom.bufferLabel.textContent = `${state.bufferCount} / ${state.windowLength}`;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  7. INCIDENT RECORDING & DASHBOARD UPDATES
  // ═══════════════════════════════════════════════════════════════════════════
  function recordDetection(event) {
    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const isAlert = event.hnids.is_alert;

    state.detections.unshift(event);
    if (state.detections.length > 80) state.detections.pop();

    state.classCounts[cls] = (state.classCounts[cls] || 0) + 1;
    if (isAlert) state.totalAlerts++;
    state.confidenceSum += conf;
    state.confidenceCount++;

    updateDashboard();
    addFeedEntry(event);

    if (isAlert && cls !== 'BENIGN') {
      showToast(`Threat Intercepted: ${cls} (${(conf * 100).toFixed(1)}% conf)`);
    }
  }

  function updateDashboard() {
    if (dom.totalRequests) dom.totalRequests.textContent = state.totalRequests;
    if (dom.totalAlerts) dom.totalAlerts.textContent = state.totalAlerts;

    if (state.confidenceCount > 0 && dom.avgConfidence) {
      const avg = (state.confidenceSum / state.confidenceCount * 100).toFixed(1);
      dom.avgConfidence.textContent = `${avg}%`;
    }

    if (dom.detectionCount) dom.detectionCount.textContent = state.detections.length;
    updateMatrix();
  }

  function addFeedEntry(event) {
    const feed = dom.detectionFeed;
    if (!feed) return;
    const empty = feed.querySelector('.feed-empty-state');
    if (empty) empty.remove();

    const cls = event.hnids.predicted_class;
    const conf = (event.hnids.confidence * 100).toFixed(1);
    const isAlert = event.hnids.is_alert;
    const time = new Date(event.event.created).toLocaleTimeString();
    const tagClass = isAlert ? 'event-badge--alert' : 'event-badge--signal';
    const tagText = isAlert ? 'THREAT' : 'BENIGN';
    const color = CLASS_COLORS[cls] || 'var(--text-primary)';

    const row = document.createElement('div');
    row.className = 'feed-event-row';
    row.innerHTML = `
      <span class="event-badge ${tagClass}">${tagText}</span>
      <span class="event-class" style="color: ${color};">${cls}</span>
      <span class="event-conf">${conf}%</span>
      <span class="event-time">${time}</span>
    `;

    feed.insertBefore(row, feed.firstChild);

    while (feed.children.length > 50) {
      feed.removeChild(feed.lastChild);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  8. METRICS
  // ═══════════════════════════════════════════════════════════════════════════
  async function loadMetrics() {
    if (!dom.metricsContent) return;
    dom.metricsContent.textContent = 'Querying Prometheus /metrics endpoint…';
    try {
      const res = await fetch(`${API_BASE}/metrics`);
      if (res.ok) {
        const text = await res.text();
        dom.metricsContent.textContent = text || 'No metrics returned.';
      } else {
        dom.metricsContent.textContent = `HTTP ${res.status}: Exporter endpoint unavailable.`;
      }
    } catch {
      dom.metricsContent.textContent = 'Failed to reach /metrics endpoint.';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  9. TOAST
  // ═══════════════════════════════════════════════════════════════════════════
  let toastTimer = null;

  function showToast(msg) {
    if (!dom.toast || !dom.toastText) return;
    dom.toastText.textContent = msg;
    dom.toast.classList.remove('hidden');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      dom.toast.classList.add('hidden');
    }, 4500);
  }

  function hideToast() {
    if (dom.toast) dom.toast.classList.add('hidden');
    clearTimeout(toastTimer);
  }

  // Fallback 20-flow payload
  const FALLBACK_PAYLOAD = {
    "flows": Array.from({ length: 20 }, (_, i) => ({
      "session_id": "seed-001",
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
  //  10. INITIALIZATION
  // ═══════════════════════════════════════════════════════════════════════════
  function init() {
    initNav();
    buildMatrix();
    initPresetChips();
    updateDashboard();

    // Event listeners
    if (dom.btnDetect) dom.btnDetect.addEventListener('click', runDetect);
    if (dom.btnLoadExample) dom.btnLoadExample.addEventListener('click', () => loadPresetSample('ddos'));
    if (dom.btnStream) dom.btnStream.addEventListener('click', pushFlow);
    if (dom.btnRefreshMetrics) dom.btnRefreshMetrics.addEventListener('click', loadMetrics);
    if (dom.toastClose) dom.toastClose.addEventListener('click', hideToast);

    // Keyboard shortcut (Cmd+Enter or Ctrl+Enter)
    if (dom.detectInput) {
      dom.detectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          runDetect();
        }
      });
    }
    if (dom.streamInput) {
      dom.streamInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          pushFlow();
        }
      });
    }

    // Default load DDoS preset for instant readiness
    loadPresetSample('ddos');

    // Health checks
    checkHealth();
    setInterval(checkHealth, 12000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

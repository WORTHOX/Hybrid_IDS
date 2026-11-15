/* ═══════════════════════════════════════════════════════════════════════════
   HNIDS Dashboard — Client Application
   Routes through /api/* proxy → FastAPI backend
   ═══════════════════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ─── Configuration ───
  const API_BASE = '/api';

  const CLASSES = ['BENIGN', 'DoS', 'DDoS', 'PortScan', 'BruteForce', 'WebAttack', 'Bot', 'Rare'];

  // Muted, professional palette matching CSS tokens
  const CLASS_COLOURS = {
    BENIGN:     'var(--c-benign)',
    DoS:        'var(--c-dos)',
    DDoS:       'var(--c-ddos)',
    PortScan:   'var(--c-portscan)',
    BruteForce: 'var(--c-bruteforce)',
    WebAttack:  'var(--c-webattack)',
    Bot:        'var(--c-bot)',
    Rare:       'var(--c-rare)',
  };

  // Hex versions for inline styles where CSS vars aren't available
  const CLASS_HEX = {
    BENIGN:     '#5C6E4C',
    DoS:        '#C8613E',
    DDoS:       '#9E3B3B',
    PortScan:   '#B08D57',
    BruteForce: '#8B4E5A',
    WebAttack:  '#A0523C',
    Bot:        '#3D4E6D',
    Rare:       '#7A6B8A',
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
    statusMark:      $('#status-mark'),
    statusText:      $('#status-text'),
    bundleVersion:   $('#bundle-version'),
    totalRequests:   $('#total-requests'),
    totalAlerts:     $('#total-alerts'),
    avgConfidence:   $('#avg-confidence'),
    distribution:    $('#distribution'),
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
    streamLog:       $('#stream-log'),
    btnRefreshMetrics: $('#btn-refresh-metrics'),
    metricsContent:  $('#metrics-content'),
    toast:           $('#toast'),
    toastText:       $('#toast-text'),
    toastClose:      $('#toast-close'),
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  NAVIGATION
  // ═══════════════════════════════════════════════════════════════════════════
  function initNav() {
    const navLinks = $$('.nav-link');
    navLinks.forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const view = link.dataset.view;
        navLinks.forEach(l => {
          l.classList.toggle('active', l === link);
          l.removeAttribute('aria-current');
        });
        link.setAttribute('aria-current', 'page');
        $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${view}`));
        if (view === 'metrics') loadMetrics();
      });
    });

    // Keyboard shortcuts (1-4)
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const idx = parseInt(e.key) - 1;
      if (idx >= 0 && idx < navLinks.length) {
        navLinks[idx].click();
      }
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  DISTRIBUTION — quiet allocation-style bars
  // ═══════════════════════════════════════════════════════════════════════════
  function buildDistribution() {
    if (!dom.distribution) return;
    dom.distribution.innerHTML = CLASSES.map(cls => `
      <div class="dist-row" data-class="${cls}">
        <span class="dist-label">${cls}</span>
        <div class="dist-track">
          <div class="dist-fill" id="dist-fill-${cls}" style="width: 0%; background: ${CLASS_HEX[cls]}"></div>
        </div>
        <span class="dist-value" id="dist-val-${cls}">0</span>
      </div>
    `).join('');
  }

  function updateDistribution() {
    const total = Math.max(1, Object.values(state.classCounts).reduce((a, b) => a + b, 0));
    CLASSES.forEach(cls => {
      const count = state.classCounts[cls];
      const pct = (count / total * 100).toFixed(1);
      const fillEl = $(`#dist-fill-${cls}`);
      const valEl = $(`#dist-val-${cls}`);
      if (fillEl) fillEl.style.width = `${pct}%`;
      if (valEl) valEl.textContent = count;
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
        dom.statusMark.className = 'status-mark ok';
        dom.statusText.textContent = 'ready';
        if (dom.bundleVersion) {
          dom.bundleVersion.textContent = data.bundle_version || '—';
        }
        return true;
      } else {
        dom.statusMark.className = 'status-mark bad';
        dom.statusText.textContent = 'not ready';
        return false;
      }
    } catch {
      dom.statusMark.className = 'status-mark bad';
      dom.statusText.textContent = 'offline';
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
    if (!raw) return showToast('Paste a JSON payload first.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Invalid JSON — check your payload.');
    }

    dom.btnDetect.disabled = true;
    dom.btnDetect.textContent = 'Detecting…';

    try {
      const result = await apiPost('/v1/detect', payload, dom.apiKeyDetect.value);
      state.totalRequests++;
      recordDetection(result);
      showResult(result);
    } catch (err) {
      showToast(`Detection failed — ${err.message}`);
    } finally {
      dom.btnDetect.disabled = false;
      dom.btnDetect.textContent = 'Detect';
    }
  }

  function showResult(event) {
    dom.resultPlaceholder.classList.add('hidden');
    dom.resultContent.classList.remove('hidden');

    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;

    dom.resultClass.textContent = cls;
    dom.resultClass.style.color = CLASS_HEX[cls] || '#1C1A17';

    dom.resultConfidence.textContent = `${(conf * 100).toFixed(2)}% confidence`;

    // Details grid
    dom.resultDetails.innerHTML = [
      ['Kind', event.event.kind],
      ['Alert', event.hnids.is_alert ? 'Yes' : 'No'],
      ['Bundle', event.hnids.bundle_version],
      ['Validation', event.hnids.validation_status],
      ['Sanitized', event.hnids.sanitized_values],
      ['Detection', event.detection_id.slice(0, 8) + '…'],
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
              <div class="prob-row__bar-fill" style="width: ${pct}%; background: ${CLASS_HEX[name] || '#A89F93'}"></div>
            </div>
            <span class="prob-row__val">${pct}%</span>
          </div>
        `;
      }).join('');
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
        dom.detectInput.value = JSON.stringify(EXAMPLE_PAYLOAD, null, 2);
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
    if (!raw) return showToast('Paste a flow record first.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Invalid JSON — check your flow record.');
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
            <span class="stream-entry__text">Detection: ${result.detection.hnids.predicted_class} (${(result.detection.hnids.confidence * 100).toFixed(1)}%)</span>
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
      const isEmpty = logEl.querySelector('.feed-empty');
      if (isEmpty) isEmpty.remove();
      logEl.insertAdjacentHTML('afterbegin', logHtml);

    } catch (err) {
      showToast(`Stream failed — ${err.message}`);
    } finally {
      dom.btnStream.disabled = false;
    }
  }

  function updateBuffer() {
    const pct = Math.min(100, (state.bufferCount / state.windowLength) * 100);
    if (dom.bufferFill) dom.bufferFill.style.width = `${pct}%`;
    if (dom.bufferLabel) dom.bufferLabel.textContent = `${state.bufferCount} / ${state.windowLength}`;

    // Change fill colour when nearing full
    if (dom.bufferFill) {
      dom.bufferFill.style.background = pct >= 80 ? 'var(--persimmon)' : 'var(--bronze)';
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
      showToast(`${cls} detected — ${(conf * 100).toFixed(1)}% confidence`);
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
    updateDistribution();
  }

  function addFeedEntry(event) {
    const feed = dom.detectionFeed;
    if (!feed) return;
    const empty = feed.querySelector('.feed-empty');
    if (empty) empty.remove();

    const cls = event.hnids.predicted_class;
    const conf = (event.hnids.confidence * 100).toFixed(1);
    const kind = event.event.kind;
    const time = new Date(event.event.created).toLocaleTimeString();

    const entry = document.createElement('div');
    entry.className = 'detection-entry';
    entry.innerHTML = `
      <span class="detection-entry__kind detection-entry__kind--${kind}">${kind}</span>
      <span class="detection-entry__class" style="color: ${CLASS_HEX[cls] || '#1C1A17'}">${cls}</span>
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
        if (dom.metricsContent) dom.metricsContent.textContent = text || 'No metrics available yet.';
      } else {
        if (dom.metricsContent) dom.metricsContent.textContent = `Error loading metrics: HTTP ${res.status}`;
      }
    } catch {
      if (dom.metricsContent) dom.metricsContent.textContent = 'Cannot reach metrics endpoint. Is the server running?';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  TOAST — quiet notification
  // ═══════════════════════════════════════════════════════════════════════════
  let toastTimeout = null;

  function showToast(message) {
    if (!dom.toast || !dom.toastText) return;
    dom.toastText.textContent = message;
    dom.toast.classList.remove('hidden');

    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => {
      dom.toast.classList.add('hidden');
    }, 5000);
  }

  function hideToast() {
    if (dom.toast) dom.toast.classList.add('hidden');
    clearTimeout(toastTimeout);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  INIT
  // ═══════════════════════════════════════════════════════════════════════════
  function init() {
    initNav();
    buildDistribution();
    updateDashboard();

    // Event listeners
    if (dom.btnDetect) dom.btnDetect.addEventListener('click', runDetect);
    if (dom.btnLoadExample) dom.btnLoadExample.addEventListener('click', loadExample);
    if (dom.btnStream) dom.btnStream.addEventListener('click', pushFlow);
    if (dom.btnRefreshMetrics) dom.btnRefreshMetrics.addEventListener('click', loadMetrics);
    if (dom.toastClose) dom.toastClose.addEventListener('click', hideToast);

    // ⌘+Enter in textareas triggers detect/stream
    if (dom.detectInput) {
      dom.detectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.metaKey) runDetect();
      });
    }
    if (dom.streamInput) {
      dom.streamInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.metaKey) pushFlow();
      });
    }

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

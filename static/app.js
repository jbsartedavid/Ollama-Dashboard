const modelSelect = document.getElementById('modelSelect');
const promptInput = document.getElementById('promptInput');
const statusBadge = document.getElementById('statusBadge');
const generateBtn = document.getElementById('generateBtn');
const generateError = document.getElementById('generateError');
const numberFormat = new Intl.NumberFormat();

function formatDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '—';
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex ? 1 : 0)} ${units[unitIndex]}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function setServerStatus(online, label = online ? 'Online' : 'Offline') {
  statusBadge.classList.toggle('status-online', online);
  statusBadge.classList.toggle('status-offline', !online);
  statusBadge.innerHTML = `<span class="status-dot"></span>${escapeHtml(label)}`;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function renderModels(models, runningModels) {
  const runningNames = new Set(runningModels.map((model) => model.name));
  const selectValue = modelSelect.value;
  modelSelect.replaceChildren();

  if (models.length === 0) {
    const option = document.createElement('option');
    option.textContent = 'No models available';
    option.value = '';
    modelSelect.appendChild(option);
  } else {
    models.forEach((model) => {
      const option = document.createElement('option');
      option.value = model.name;
      option.textContent = model.name;
      option.selected = model.name === selectValue;
      modelSelect.appendChild(option);
    });
  }

  document.getElementById('modelsList').innerHTML = models.length
    ? models.map((model) => {
      const details = [model.parameterSize, model.quantization].filter(Boolean).join(' · ');
      return `<div class="model-entry"><span class="model-name"><i class="model-dot"></i>${escapeHtml(model.name)}</span><span class="model-detail">${escapeHtml(details || formatBytes(model.size))}</span></div>`;
    }).join('')
    : '<div class="muted-copy">No installed models found.</div>';

  const loaded = models.filter((model) => runningNames.has(model.name));
  document.getElementById('installedModels').textContent = numberFormat.format(models.length);
  document.getElementById('loadedModels').textContent = numberFormat.format(loaded.length);
  document.getElementById('runningModels').innerHTML = loaded.length
    ? loaded.map((model) => {
      const running = runningModels.find((item) => item.name === model.name) || {};
      const memory = running.size_vram ? formatBytes(running.size_vram) : '';
      return `<div class="model-entry"><span class="model-name"><i class="model-dot"></i>${escapeHtml(model.name)}</span><span class="model-detail">${escapeHtml(memory || 'Loaded')}</span></div>`;
    }).join('')
    : '<div class="muted-copy">No models currently loaded.</div>';
}

async function refreshHealth() {
  try {
    const data = await getJson('/api/health');
    setServerStatus(data.status === 'online', data.status === 'online' ? 'Online' : 'Offline');
    document.getElementById('serverEndpoint').textContent = data.endpoint;
    document.getElementById('serverLatency').textContent = formatDuration(data.apiLatencyMs);
    document.getElementById('serverVersion').textContent = data.version || 'Unknown';
    renderModels(data.models || [], data.runningModels || []);
    if (data.warnings?.length) {
      document.getElementById('serverVersion').title = data.warnings.join('\n');
    }
  } catch (error) {
    setServerStatus(false);
    document.getElementById('serverLatency').textContent = '—';
    document.getElementById('serverVersion').textContent = 'Unavailable';
    document.getElementById('serverEndpoint').textContent = 'Connection failed';
    document.getElementById('runningModels').innerHTML = `<div class="muted-copy">${escapeHtml(error.message)}</div>`;
    document.getElementById('modelsList').innerHTML = '<div class="muted-copy">Ollama is unreachable.</div>';
  }
}

function renderLatestResponse(activity) {
  const container = document.getElementById('latestResponse');
  const modelTag = document.getElementById('responseModel');
  const metrics = document.getElementById('responseMetrics');
  if (!activity) {
    container.className = 'latest-response empty-state';
    container.textContent = 'Responses from recent requests will appear here.';
    modelTag.textContent = 'No response';
    metrics.replaceChildren();
    return;
  }

  modelTag.textContent = `${activity.model} · ${formatDuration(activity.wall_duration_ms)}`;
  if (activity.status !== 'success') {
    container.className = 'latest-response response-error';
    container.textContent = activity.error || 'Generation failed.';
    metrics.replaceChildren();
    return;
  }

  const responseMetrics = [
    activity.prompt_eval_count ? `Prompt ${numberFormat.format(activity.prompt_eval_count)} tok` : null,
    activity.eval_count ? `Generated ${numberFormat.format(activity.eval_count)} tok` : null,
    activity.tokens_per_second > 0 ? `${activity.tokens_per_second.toFixed(1)} tok/s` : null,
    activity.load_duration_ns ? `Model load ${formatDuration(activity.load_duration_ns / 1e6)}` : null,
    activity.total_duration_ns ? `Ollama ${formatDuration(activity.total_duration_ns / 1e6)}` : null
  ].filter(Boolean);
  metrics.innerHTML = responseMetrics.map((item) => `<span>${escapeHtml(item)}</span>`).join('');
  container.className = 'latest-response';
  container.innerHTML = `<div class="response-prompt">${escapeHtml(activity.prompt)}</div>${escapeHtml(activity.response || 'The model returned no text.')}`;
}

function renderActivity(recent) {
  const list = document.getElementById('activityList');
  document.getElementById('requestCount').textContent = `${recent.length} recent`;
  if (!recent.length) {
    list.innerHTML = '<div class="empty-state">No model activity yet. Send a prompt to begin monitoring.</div>';
    renderLatestResponse(null);
    return;
  }

  list.innerHTML = recent.map((item) => {
    const time = new Date(item.created_at);
    const timeLabel = Number.isNaN(time.getTime())
      ? '—'
      : time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const tokenLabel = item.status === 'success' && item.eval_count
      ? `${numberFormat.format(item.eval_count)} tok · ${item.tokens_per_second.toFixed(1)} tok/s`
      : item.status === 'error' ? 'Failed' : formatDuration(item.wall_duration_ms);
    return `<article class="activity-row">
      <time class="activity-time">${escapeHtml(timeLabel)}</time>
      <span class="activity-model" title="${escapeHtml(item.model)}">${escapeHtml(item.model)}</span>
      <span class="activity-prompt" title="${escapeHtml(item.prompt)}">${escapeHtml(item.prompt)}</span>
      <div class="activity-meta"><i class="result-status ${item.status === 'error' ? 'failed' : ''}"></i><span>${escapeHtml(tokenLabel)}</span></div>
    </article>`;
  }).join('');
  renderLatestResponse(recent.find((item) => item.status === 'success') || recent[0]);
}

function renderModelActivity(models) {
  const container = document.getElementById('modelActivity');
  if (!models.length) {
    container.innerHTML = '<div class="muted-copy">No model activity recorded yet.</div>';
    return;
  }
  container.innerHTML = models.map((model) => `
    <div class="model-activity-row">
      <strong title="${escapeHtml(model.model)}">${escapeHtml(model.model)}</strong>
      <span>${numberFormat.format(model.requests)} req</span>
      <span>${numberFormat.format(model.generated_tokens || 0)} tokens</span>
      <span>${numberFormat.format(model.failed_requests || 0)} failed</span>
    </div>`).join('');
}

async function refreshMetrics() {
  const data = await getJson('/api/metrics');
  const summary = data.summary;
  document.getElementById('totalRequests').textContent = numberFormat.format(summary.totalRequests);
  document.getElementById('requestSuccess').textContent = `${numberFormat.format(summary.successfulRequests)} successful · ${numberFormat.format(summary.failedRequests)} failed`;
  document.getElementById('avgLatency').textContent = formatDuration(summary.avgWallDurationMs);
  document.getElementById('tokensPerSecond').textContent = summary.tokensPerSecond > 0
    ? `${summary.tokensPerSecond.toFixed(1)}`
    : '—';
  document.getElementById('tokenCount').textContent = numberFormat.format(summary.promptTokens + summary.generatedTokens);
  document.getElementById('tokenBreakdown').textContent =
    `Prompt ${numberFormat.format(summary.promptTokens)} · Generated ${numberFormat.format(summary.generatedTokens)}`;
  renderModelActivity(data.byModel || []);
  renderActivity(data.recent || []);
  document.getElementById('lastUpdated').textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
}

async function refreshDashboard() {
  const results = await Promise.allSettled([refreshHealth(), refreshMetrics()]);
  if (results[1].status === 'rejected') {
    document.getElementById('activityList').innerHTML =
      `<div class="muted-copy">${escapeHtml(results[1].reason.message || 'Unable to load activity metrics.')}</div>`;
  }
}

async function generateText() {
  const prompt = promptInput.value.trim();
  const model = modelSelect.value;
  generateError.textContent = '';
  if (!prompt) {
    generateError.textContent = 'Enter a prompt before generating.';
    return;
  }

  generateBtn.disabled = true;
  generateBtn.innerHTML = 'Generating… <span aria-hidden="true">◌</span>';
  try {
    await getJson('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, model })
    });
    await refreshDashboard();
  } catch (error) {
    generateError.textContent = error.message;
    await refreshDashboard();
  } finally {
    generateBtn.disabled = false;
    generateBtn.innerHTML = 'Generate response <span aria-hidden="true">→</span>';
  }
}

document.getElementById('refreshBtn').addEventListener('click', refreshDashboard);
document.getElementById('refreshModelsBtn').addEventListener('click', refreshHealth);
generateBtn.addEventListener('click', generateText);
promptInput.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') generateText();
});

refreshDashboard();
window.setInterval(refreshDashboard, 10000);

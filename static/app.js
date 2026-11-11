const modelSelect = document.getElementById('modelSelect');
const promptInput = document.getElementById('promptInput');
const statusBadge = document.getElementById('statusBadge');
const generateBtn = document.getElementById('generateBtn');
const generateError = document.getElementById('generateError');
const settingsForm = document.getElementById('settingsForm');
const settingsMessage = document.getElementById('settingsMessage');
const apiKeyInput = document.getElementById('ollamaApiKey');
const clearApiKeyInput = document.getElementById('clearApiKey');
const numberFormat = new Intl.NumberFormat();
let refreshTimer = null;

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

function formatRate(bytesPerSecond) {
  if (!Number.isFinite(bytesPerSecond)) return '—';
  return `${bytesPerSecond <= 0 ? '0 B' : formatBytes(bytesPerSecond)}/s`;
}

function setUsageBar(id, percentage) {
  const bar = document.getElementById(id);
  const value = Number.isFinite(percentage) ? Math.min(100, Math.max(0, percentage)) : 0;
  bar.setAttribute('aria-valuenow', value.toFixed(1));
  bar.firstElementChild.style.width = `${value}%`;
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
    const placeholder = document.createElement('option');
    placeholder.textContent = 'Select a model for generation';
    placeholder.value = '';
    placeholder.disabled = true;
    placeholder.selected = true;
    modelSelect.appendChild(placeholder);
    models.forEach((model) => {
      const option = document.createElement('option');
      option.value = model.name;
      option.textContent = model.name;
      modelSelect.appendChild(option);
    });
    if (models.some((model) => model.name === selectValue)) modelSelect.value = selectValue;
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

function renderSystemMetrics(data) {
  const cpu = data.cpu;
  const memory = data.memory;
  const virtualMemory = data.virtualMemory;
  const disk = data.disk;
  document.getElementById('systemCpuValue').textContent = `${Math.round(cpu.percent)}%`;
  document.getElementById('systemCpuDetail').textContent = `${numberFormat.format(cpu.logicalCores)} logical cores`;
  setUsageBar('systemCpuBar', cpu.percent);

  document.getElementById('systemMemoryValue').textContent = `${memory.percent.toFixed(1)}%`;
  document.getElementById('systemMemoryDetail').textContent =
    `${formatBytes(memory.usedBytes)} / ${formatBytes(memory.totalBytes)} · ${formatBytes(memory.availableBytes)} available`;
  setUsageBar('systemMemoryBar', memory.percent);

  document.getElementById('systemVirtualMemoryValue').textContent = `${virtualMemory.percent.toFixed(1)}%`;
  document.getElementById('systemVirtualMemoryDetail').textContent =
    `${formatBytes(virtualMemory.usedBytes)} / ${formatBytes(virtualMemory.totalBytes)} pagefile / swap`;
  setUsageBar('systemVirtualMemoryBar', virtualMemory.percent);

  document.getElementById('systemDiskLabel').textContent = `${disk.volume} drive`;
  document.getElementById('systemDiskValue').textContent = `${disk.percent.toFixed(1)}%`;
  document.getElementById('systemDiskDetail').textContent =
    `${formatBytes(disk.usedBytes)} / ${formatBytes(disk.totalBytes)} used`;
  document.getElementById('systemDiskIo').textContent =
    `Read ${formatRate(disk.readBytesPerSecond)} · Write ${formatRate(disk.writeBytesPerSecond)}`;
  setUsageBar('systemDiskBar', disk.percent);

  const gpuDevices = data.gpu.devices || [];
  document.getElementById('systemGpuState').textContent =
    data.gpu.status === 'available' ? `${gpuDevices.length} detected` : 'Unavailable';
  document.getElementById('systemGpuDevices').innerHTML = gpuDevices.length
    ? gpuDevices.map((gpu) => {
      const utilization = Number.isFinite(gpu.utilizationPercent) ? `${gpu.utilizationPercent.toFixed(0)}%` : '—';
      const memoryUsed = Number.isFinite(gpu.memoryUsedMb) ? gpu.memoryUsedMb * 1024 * 1024 : NaN;
      const memoryTotal = Number.isFinite(gpu.memoryTotalMb) ? gpu.memoryTotalMb * 1024 * 1024 : NaN;
      const memoryPercent = Number.isFinite(gpu.memoryUsedMb) && gpu.memoryTotalMb > 0
        ? Math.min(100, gpu.memoryUsedMb * 100 / gpu.memoryTotalMb)
        : 0;
      const details = [
        `VRAM ${formatBytes(memoryUsed)} / ${formatBytes(memoryTotal)}`,
        Number.isFinite(gpu.temperatureC) ? `${gpu.temperatureC.toFixed(0)}°C` : null,
        Number.isFinite(gpu.powerWatts) ? `${gpu.powerWatts.toFixed(0)} W` : null
      ].filter(Boolean).join(' · ');
      return `<div class="gpu-device">
        <span class="gpu-device-name" title="${escapeHtml(gpu.name)}">${escapeHtml(gpu.name)} · ${utilization}</span>
        <div class="usage-track" role="progressbar" aria-label="${escapeHtml(gpu.name)} VRAM utilization" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${memoryPercent.toFixed(1)}"><span style="width:${memoryPercent}%"></span></div>
        <span class="hardware-detail">${escapeHtml(details)}</span>
      </div>`;
    }).join('')
    : `<div class="muted-copy" title="${escapeHtml(data.gpu.error || '')}">${escapeHtml(data.gpu.error || 'No GPU telemetry available.')}</div>`;
  const sampledAt = new Date(data.sampledAt);
  document.getElementById('systemSampleTime').textContent = Number.isNaN(sampledAt.getTime())
    ? 'Just sampled'
    : `Sampled ${sampledAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
}

async function refreshSystemMetrics() {
  const data = await getJson('/api/system');
  renderSystemMetrics(data);
}

async function loadSettings() {
  const [settings, memory] = await Promise.all([
    getJson('/api/settings'),
    getJson('/api/memory/settings')
  ]);
  document.getElementById('ollamaBaseUrl').value = settings.baseUrl;
  document.getElementById('keepAliveInput').value = settings.keepAlive;
  document.getElementById('clientGatewayUrl').value = settings.clientGatewayUrl;
  document.getElementById('remoteMonitoringStatus').textContent = settings.gatewayEnabled
    ? 'Monitoring is active on port 11434. Clients use the same Ollama address; no dashboard key or client changes are needed.'
    : 'The monitoring proxy is available when the dashboard is configured to own port 11434.';
  const refreshInterval = document.getElementById('refreshInterval');
  refreshInterval.value = String(settings.autoRefreshSeconds);
  refreshInterval.dataset.savedValue = String(settings.autoRefreshSeconds);
  configureRefresh(settings.autoRefreshSeconds);
  apiKeyInput.value = '';
  apiKeyInput.placeholder = settings.apiKeyConfigured ? 'Saved securely ? leave blank to keep' : 'Not configured';
  document.getElementById('apiKeyState').textContent = settings.apiKeyConfigured
    ? 'A key is saved encrypted for this Windows account. Leave blank to keep it, or select Remove to clear it.'
    : 'Only needed for servers with bearer-token authentication. Saved keys are encrypted for this Windows account.';
  clearApiKeyInput.checked = false;
  apiKeyInput.disabled = false;
  document.getElementById('memoryEnabled').checked = memory.enabled;
  document.getElementById('memoryDirectory').value = memory.directory;
  document.getElementById('memoryMaxMb').value = String(memory.maxMb);
  document.getElementById('memoryModelScoped').checked = memory.modelScoped;
  renderMemoryStatus(memory);
}

function renderMemoryStatus(memory) {
  const used = memory.usedBytes > 0 ? formatBytes(memory.usedBytes) : '0 B';
  const status = document.getElementById('memoryStatus');
  status.textContent = `${numberFormat.format(memory.conversationCount)} conversations · ${used} / ${numberFormat.format(memory.maxMb)} MB used · ${memory.enabled ? 'Memory on' : 'Memory off'}`;
  if (memory.lastError) {
    status.textContent += ` · ${memory.lastError}`;
    status.classList.add('error');
  } else {
    status.classList.remove('error');
  }
}

async function refreshMemoryStatus() {
  const memory = await getJson('/api/memory/settings');
  renderMemoryStatus(memory);
}

async function saveSettings(event) {
  event.preventDefault();
  settingsMessage.textContent = '';
  settingsMessage.classList.remove('error');
  const saveButton = document.getElementById('saveSettingsBtn');
  saveButton.disabled = true;
  saveButton.textContent = 'Saving…';
  try {
    const saved = await getJson('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        baseUrl: document.getElementById('ollamaBaseUrl').value,
        keepAlive: document.getElementById('keepAliveInput').value,
        apiKey: apiKeyInput.value,
        clearApiKey: clearApiKeyInput.checked
      })
    });
    if (!saved.saved) throw new Error('Settings were not saved.');
    settingsMessage.textContent = 'Settings saved.';
    modelSelect.value = '';
    await loadSettings();
    await refreshDashboard();
  } catch (error) {
    settingsMessage.textContent = error.message;
    settingsMessage.classList.add('error');
  } finally {
    saveButton.disabled = false;
    saveButton.innerHTML = 'Save connection settings <span aria-hidden="true">→</span>';
  }
}

async function saveMemorySettings(event) {
  event.preventDefault();
  const message = document.getElementById('memorySettingsMessage');
  const button = document.getElementById('saveMemorySettingsBtn');
  message.textContent = '';
  message.classList.remove('error');
  button.disabled = true;
  button.textContent = 'Saving…';
  try {
    const memory = await getJson('/api/memory/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: document.getElementById('memoryEnabled').checked,
        directory: document.getElementById('memoryDirectory').value.trim(),
        maxMb: Number(document.getElementById('memoryMaxMb').value),
        modelScoped: document.getElementById('memoryModelScoped').checked
      })
    });
    renderMemoryStatus(memory);
    message.textContent = 'Memory settings saved.';
  } catch (error) {
    message.textContent = error.message;
    message.classList.add('error');
  } finally {
    button.disabled = false;
    button.innerHTML = 'Save memory settings <span aria-hidden="true">→</span>';
  }
}

async function clearMemories() {
  if (!window.confirm('Delete all saved conversation memories? This cannot be undone.')) return;
  const button = document.getElementById('clearMemoryBtn');
  const message = document.getElementById('memorySettingsMessage');
  button.disabled = true;
  message.textContent = '';
  message.classList.remove('error');
  try {
    await getJson('/api/memory/clear', { method: 'POST' });
    await refreshMemoryStatus();
    message.textContent = 'Saved memories cleared.';
  } catch (error) {
    message.textContent = error.message;
    message.classList.add('error');
  } finally {
    button.disabled = false;
  }
}

async function copySettingValue(input, label) {
  if (!input.value) {
    settingsMessage.textContent = `No ${label} is available to copy.`;
    settingsMessage.classList.add('error');
    return;
  }
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    input.focus();
    input.select();
    if (!document.execCommand('copy')) {
      settingsMessage.textContent = `Could not copy the ${label}; select and copy it manually.`;
      settingsMessage.classList.add('error');
      return;
    }
  }
  settingsMessage.classList.remove('error');
  settingsMessage.textContent = `${label} copied.`;
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
      <div class="activity-meta"><i class="result-status ${item.status === 'error' ? 'failed' : ''}"></i><span>${escapeHtml(tokenLabel)}</span><span class="activity-origin" title="${item.client_ip ? 'Client IP' : 'Request source'}">${item.client_ip ? `Client ${escapeHtml(item.client_ip)}` : 'Dashboard'}</span></div>
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
  const results = await Promise.allSettled([
    refreshHealth(), refreshMetrics(), refreshSystemMetrics(), refreshMemoryStatus()
  ]);
  if (results[1].status === 'rejected') {
    document.getElementById('activityList').innerHTML =
      `<div class="muted-copy">${escapeHtml(results[1].reason.message || 'Unable to load activity metrics.')}</div>`;
  }
  if (results[2].status === 'rejected') {
    const message = results[2].reason.message || 'Unable to load system metrics.';
    document.getElementById('systemSampleTime').textContent = 'Unavailable';
    document.getElementById('systemGpuState').textContent = 'Unavailable';
    document.getElementById('systemGpuDevices').innerHTML = `<div class="muted-copy">${escapeHtml(message)}</div>`;
  }
  if (results[3].status === 'rejected') {
    const status = document.getElementById('memoryStatus');
    status.textContent = results[3].reason.message || 'Unable to load memory status.';
    status.classList.add('error');
  }
}

function configureRefresh(seconds) {
  if (refreshTimer !== null) window.clearInterval(refreshTimer);
  refreshTimer = seconds > 0
    ? window.setInterval(refreshDashboard, seconds * 1000)
    : null;
  document.getElementById('refreshIntervalSummary').textContent = seconds > 0
    ? `Automatic refresh: ${seconds} seconds`
    : 'Automatic refresh: off';
}

async function saveRefreshInterval(event) {
  const select = event.currentTarget;
  const previous = select.dataset.savedValue || '10';
  const seconds = Number(select.value);
  select.disabled = true;
  try {
    const saved = await getJson('/api/settings/refresh-interval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds })
    });
    select.dataset.savedValue = String(saved.autoRefreshSeconds);
    configureRefresh(saved.autoRefreshSeconds);
  } catch (error) {
    select.value = previous;
    settingsMessage.textContent = error.message;
    settingsMessage.classList.add('error');
  } finally {
    select.disabled = false;
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
  if (!model) {
    generateError.textContent = 'Choose a model to generate a response. Monitoring includes all models.';
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
settingsForm.addEventListener('submit', saveSettings);
document.getElementById('memorySettingsForm').addEventListener('submit', saveMemorySettings);
document.getElementById('clearMemoryBtn').addEventListener('click', clearMemories);
document.getElementById('copyGatewayUrlBtn').addEventListener('click', () =>
  copySettingValue(document.getElementById('clientGatewayUrl'), 'client URL'));
document.getElementById('refreshInterval').addEventListener('change', saveRefreshInterval);
clearApiKeyInput.addEventListener('change', () => {
  apiKeyInput.disabled = clearApiKeyInput.checked;
  if (clearApiKeyInput.checked) apiKeyInput.value = '';
});
generateBtn.addEventListener('click', generateText);
promptInput.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') generateText();
});

loadSettings()
  .then(refreshDashboard)
  .catch((error) => {
    settingsMessage.textContent = error.message;
    settingsMessage.classList.add('error');
    refreshDashboard();
  });

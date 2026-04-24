/* ═══════════════════════════════════════════════════
   PROSPECTOR — Settings & Usage Dashboard Module
   Manages API settings form and usage monitoring
   ═══════════════════════════════════════════════════ */

import { safeFetch } from './api.js';

const API = window.location.origin;

// ─── Helpers ───

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showToast(msg, type = 'info') {
  const existing = document.getElementById('settingsToast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = 'settingsToast';
  toast.className = `settings-toast settings-toast--${type}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ─── Service metadata ───

const SERVICE_META = {
  serper:    { label: 'Serper',    icon: '🔍' },
  ollama:    { label: 'Ollama IA', icon: '🤖' },
  brasilapi: { label: 'BrasilAPI', icon: '📋' },
  maps:      { label: 'Maps',      icon: '📍' },
};

// ─── Load Settings ───

async function loadSettings() {
  try {
    const data = await safeFetch(`${API}/api/settings`);
    const fields = ['SERPER_KEY', 'OLLAMA_KEY', 'OLLAMA_BASE', 'OLLAMA_MODEL', 'OLLAMA_FALLBACKS'];
    for (const field of fields) {
      const el = document.getElementById(field) || document.querySelector(`[name="${field}"]`);
      if (el && data[field] !== undefined) {
        el.value = data[field];
      }
    }
    _updateKeyStatus('serperKeyStatus', data['SERPER_KEY']);
    _updateKeyStatus('ollamaKeyStatus', data['OLLAMA_KEY']);
  } catch (e) {
    showToast(`Erro ao carregar configurações: ${e.message}`, 'error');
  }
}

function _updateKeyStatus(statusId, value) {
  const el = document.getElementById(statusId);
  if (!el) return;
  const isConfigured = value && value.trim() !== '';
  el.textContent = isConfigured ? '✅ Configurada' : '❌ Não configurada';
  el.className = 'key-status ' + (isConfigured ? 'configured' : 'missing');
}

// ─── Save Settings ───

async function saveSettings() {
  const form = document.getElementById('settingsForm');
  const btn = document.getElementById('saveSettingsBtn');
  if (!form) return;

  if (btn) { btn.disabled = true; btn.textContent = 'Salvando...'; }

  try {
    const payload = {};
    const fields = ['SERPER_KEY', 'OLLAMA_KEY', 'OLLAMA_BASE', 'OLLAMA_MODEL', 'OLLAMA_FALLBACKS'];
    for (const field of fields) {
      const el = document.getElementById(field) || document.querySelector(`[name="${field}"]`);
      if (el) payload[field] = el.value;
    }

    const data = await safeFetch(`${API}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    showToast('Configurações salvas com sucesso!', 'success');
    if (data) {
      _updateKeyStatus('serperKeyStatus', data['SERPER_KEY']);
      _updateKeyStatus('ollamaKeyStatus', data['OLLAMA_KEY']);
    }
  } catch (e) {
    showToast(`Erro ao salvar: ${e.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Salvar Configurações'; }
  }
}

// ─── Load Usage ───

async function loadUsage() {
  try {
    const data = await safeFetch(`${API}/api/usage`);
    renderUsageCards(data);
    renderUsageTable(data);
  } catch (e) {
    const c = document.getElementById('usageCards');
    if (c) c.innerHTML = `<p class="usage-empty">Erro ao carregar dados de uso.</p>`;
  }
}

// ─── Render Usage Cards ───

function renderUsageCards(data) {
  const container = document.getElementById('usageCards');
  if (!container) return;
  const today = (data && data.today) || {};
  const services = ['serper', 'ollama', 'brasilapi', 'maps'];
  const html = services.map(svc => {
    const meta = SERVICE_META[svc] || { label: svc, icon: '📊' };
    const count = Number(today[svc] ?? 0);
    return `<div class="usage-card">
      <div class="usage-card-icon">${meta.icon}</div>
      <div class="usage-card-name">${esc(meta.label)}</div>
      <div class="usage-card-count">${esc(String(count))}</div>
      <div class="usage-card-label">hoje</div>
    </div>`;
  }).join('');
  container.innerHTML = html;
}

// ─── Render Usage Table ───

function renderUsageTable(data) {
  const container = document.getElementById('usageTable');
  if (!container) return;
  const history = (data && data.history) || [];
  if (history.length === 0) {
    container.innerHTML = '<p class="usage-empty">Nenhum dado registrado ainda.</p>';
    return;
  }
  const headers = ['Data', 'Serper', 'Ollama IA', 'BrasilAPI', 'Maps']
    .map(h => `<th>${esc(h)}</th>`).join('');
  const rows = history.map(row => `<tr>
    <td>${esc(row.date || '')}</td>
    <td>${esc(String(row.serper ?? 0))}</td>
    <td>${esc(String(row.ollama ?? 0))}</td>
    <td>${esc(String(row.brasilapi ?? 0))}</td>
    <td>${esc(String(row.maps ?? 0))}</td>
  </tr>`).join('');
  container.innerHTML = `<table class="usage-table" role="table" aria-label="Histórico de uso dos últimos 7 dias">
    <thead><tr>${headers}</tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

export { loadSettings, saveSettings, loadUsage, renderUsageCards, renderUsageTable };

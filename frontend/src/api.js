const API_BASE = '/api';

// Ключи аутентификации, которые мы сохраняем при логине.
// Только их и стираем при logout/401 — чужие ключи (theme, drafts и пр.) не трогаем.
export const AUTH_STORAGE_KEYS = ['token', 'role', 'username'];

const AUTH_PATHS = ['/auth/login', '/auth/user-login', '/auth/register'];

function getToken() {
  return localStorage.getItem('token') || '';
}

function headers(extra = {}) {
  return {
    'Authorization': `Bearer ${getToken()}`,
    ...extra,
  };
}

function clearAuthStorage() {
  for (const key of AUTH_STORAGE_KEYS) {
    try { localStorage.removeItem(key); } catch {}
  }
}

// Флаг защищает от каскада событий, если за короткое время прилетит
// несколько 401 (параллельные запросы): App.jsx должен среагировать один раз.
// resetUnauthorizedFlag() вызывает App после успешного логина, чтобы повторный
// прилёт 401 опять триггерил выход.
let unauthorizedNotified = false;

export function resetUnauthorizedFlag() {
  unauthorizedNotified = false;
}

function handleUnauthorized(path) {
  if (AUTH_PATHS.some(p => path.startsWith(p))) return;
  if (unauthorizedNotified) return;
  unauthorizedNotified = true;
  clearAuthStorage();
  // App.jsx слушает это событие и навигирует на /login через react-router.
  window.dispatchEvent(new CustomEvent('app:unauthorized'));
}

export function logoutClearStorage() {
  clearAuthStorage();
  resetUnauthorizedFlag();
}

async function checkAuth(res, path) {
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error('Сессия истекла. Войдите снова.');
  }
}

export async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  await checkAuth(res, path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${res.status}`);
  }
  return res.json();
}

export async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers() });
  await checkAuth(res, path);
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json();
}

export async function apiPatch(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  await checkAuth(res, path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${res.status}`);
  }
  return res.json();
}

export async function apiDelete(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: headers(),
  });
  await checkAuth(res, path);
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json();
}

export async function downloadFile(path, fallbackFilename = 'file') {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers() });
  await checkAuth(res, path);
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);

  let filename = fallbackFilename;
  const disposition = res.headers.get('content-disposition') || '';
  const m = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
  if (m) filename = decodeURIComponent(m[1] || m[2] || fallbackFilename);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function apiUpload(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${getToken()}` },
    body: formData,
  });
  await checkAuth(res, path);
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json();
}

export async function apiPut(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Authorization': `Bearer ${getToken()}` },
    body: formData,
  });
  await checkAuth(res, path);
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json();
}

export async function chatPost({ message, conversation_id, mode, files = [], scenario_hint }) {
  const form = new FormData();
  form.append('message', message);
  form.append('mode', mode);
  if (conversation_id) form.append('conversation_id', conversation_id);
  if (scenario_hint) form.append('scenario_hint', scenario_hint);
  for (const f of files) form.append('files', f);
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${getToken()}` },
    body: form,
  });
  await checkAuth(res, '/chat');
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${res.status}`);
  }
  return res.json();
}

/**
 * Стриминговый чат через SSE.
 * Колбэки:
 *   onMeta({ conversation_id })
 *   onDelta(text)        — новая порция текста ответа
 *   onPartEnd()          — конец одной части (LLM продолжает)
 *   onDone()             — поток завершён
 *   onError(message)     — ошибка/таймаут
 *   onCounterpartyCheck(data) — структурированный результат проверки контрагента
 */
export async function chatStream(
  { message, conversation_id, mode, files = [], scenario_hint },
  { onMeta, onDelta, onPartEnd, onDone, onError, onCounterpartyCheck } = {},
) {
  const form = new FormData();
  form.append('message', message);
  form.append('mode', mode);
  if (conversation_id) form.append('conversation_id', conversation_id);
  if (scenario_hint) form.append('scenario_hint', scenario_hint);
  for (const f of files) form.append('files', f);

  let res;
  try {
    res = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${getToken()}` },
      body: form,
    });
  } catch (e) {
    onError?.(`Ошибка сети: ${e.message}`);
    return;
  }
  if (res.status === 401) {
    handleUnauthorized('/chat');
    onError?.('Сессия истекла. Войдите снова.');
    return;
  }
  if (res.status === 403) {
    onError?.('Нет доступа к ресурсу.');
    return;
  }
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`;
    try {
      const err = await res.json();
      if (err.detail) detail = err.detail;
    } catch {}
    onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  const handleEvent = (raw) => {
    if (!raw.startsWith('data: ')) return;
    const dataStr = raw.slice(6).trim();
    if (!dataStr) return;
    let obj;
    try {
      obj = JSON.parse(dataStr);
    } catch {
      return;
    }
    if (obj.type === 'meta') onMeta?.({
      conversation_id: obj.conversation_id,
      mode: obj.mode,
      routed_mode: obj.routed_mode,
      routing_reason: obj.routing_reason,
      routing_confidence: obj.routing_confidence,
    });
    else if (obj.type === 'delta') onDelta?.(obj.content || '');
    else if (obj.type === 'part_end') onPartEnd?.();
    else if (obj.type === 'done') onDone?.();
    else if (obj.type === 'error') onError?.(obj.message || 'Ошибка');
    else if (obj.type === 'counterparty_check') onCounterpartyCheck?.(obj.data);
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleEvent(event);
      }
    }
    if (buffer.trim()) handleEvent(buffer.trim());
  } catch (e) {
    onError?.(`Соединение прервано: ${e.message}`);
  }
}

export function login(data) {
  return apiPost('/auth/login', data);
}

export function userLogin(username) {
  return apiPost('/auth/user-login', { username });
}

// Self-registration (role always forced to 'user' by backend)
export function register(data) {
  return apiPost('/auth/register', data);
}


// ── Bitrix24: задача из чата ──────────────────────────────────────────────────

export function getBitrixTaskConfig() {
  return apiGet('/bitrix/task-config');
}

export async function createBitrixTask(convId, { title, description, blob, filename }) {
  const fd = new FormData();
  fd.append('title', title);
  fd.append('description', description || '');
  fd.append('file', blob, filename || 'chat.docx');
  const path = `/conversations/${convId}/bitrix-task`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${getToken()}` },
    body: fd,
  });
  await checkAuth(res, path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${res.status}`);
  }
  return res.json();
}

export function uploadSkillFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  return apiUpload('/skills/upload', fd);
}

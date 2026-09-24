const token = localStorage.getItem('owar-admin-token') || prompt('Enter the admin token');
if (token) localStorage.setItem('owar-admin-token', token);
const auth = { Authorization: `Bearer ${token}` };
const $ = selector => document.querySelector(selector);

async function request(url, body) {
  const response = await fetch(url, { method: body ? 'POST' : 'GET', headers: { ...auth, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}

function renderSources(sources) {
  $('#source-list').innerHTML = sources.map(source => {
    const state = source.last_error ? `Last import failed: ${source.last_error}` : source.last_success ? `Last successful import: ${new Date(source.last_success).toLocaleString()}` : 'Waiting for the first import';
    return `<div class="event-row"><div><p>${source.url}</p><p>${state}</p></div><button class="btn source-toggle" data-source="${source.rowid}" data-active="${source.active}">${source.active ? 'Disable source' : 'Enable source'}</button></div>`;
  }).join('');
  $('#source-list').querySelectorAll('button').forEach(button => button.onclick = async () => { await request(`/api/admin/sources/${button.dataset.source}`, { active: button.dataset.active !== '1' }); render(); });
}

function renderUpdate(update) {
  let box = $('#update-control');
  if (!box) { box = document.createElement('div'); box.id = 'update-control'; box.className = 'field'; $('#reset').after(box); }
  const latest = update.latestVersion || update.latest_version || update.latest || 'unknown';
  box.innerHTML = `<label>Application updates</label><p>${update.error ? 'Shared updater unavailable' : `Latest version: ${latest}`}</p><button class="btn" id="install-update" ${update.error ? 'disabled' : ''}>Install update</button>`;
  $('#install-update').onclick = async () => { $('#install-update').disabled = true; $('#install-update').textContent = 'Starting update…'; await request('/api/admin/update', {}); setTimeout(render, 3000); };
}

async function render() {
  try {
    const [data, update] = await Promise.all([request('/api/admin/status'), request('/api/admin/update')]);
    const paused = data.meta.feed_paused === 'true';
    $('#app-version').textContent = `Version ${data.version}`;
    $('#event-status').value = data.meta.status || 'Live';
    $('#event-list').innerHTML = data.events.map(event => `<div class="event-row"><div><h3>${event.name}</h3><p>${event.count} imported results · ${event.visible ? 'Visible publicly' : 'Hidden'}</p></div><label class="switch"><input type="checkbox" data-id="${event.id}" ${event.visible ? 'checked' : ''}><span class="slider"></span></label></div>`).join('');
    $('#event-list').querySelectorAll('input').forEach(input => input.onchange = async () => { await request(`/api/admin/events/${input.dataset.id}`, { visible: input.checked }); render(); });
    renderSources(data.sources);
    $('#simulate').textContent = paused ? 'Start live updates' : `Stop live updates (${data.pollSeconds}s)`;
    $('#simulate').onclick = async () => { await request('/api/admin/feed', { running: paused }); render(); };
    $('#reset').textContent = `Last import: ${data.meta.last_import || 'waiting'}`;
    $('#reset').disabled = true;
    renderUpdate(update);
  } catch (error) { $('#event-list').innerHTML = `<p>${error.message}</p>`; }
}

$('#add-source').onclick = async () => { const input = $('#source-url'); if (!input.value.trim()) return; await request('/api/admin/sources', { url: input.value.trim() }); input.value = ''; render(); };
$('#event-status').onchange = () => request('/api/admin/status', { status: $('#event-status').value });
render();

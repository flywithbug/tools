const api = {
  async getLocales() {
    const res = await fetch('/api/locales');
    return res.json();
  },
  async getEntries(search = '') {
    const url = search ? `/api/entries?search=${encodeURIComponent(search)}` : '/api/entries';
    const res = await fetch(url);
    return res.json();
  },
  async addKey(key) {
    const res = await fetch('/api/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, values: {} })
    });
    return res.json();
  },
  async updateEntry(key, values) {
    const res = await fetch(`/api/entries/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values })
    });
    return res.json();
  },
  async deleteEntry(key) {
    const res = await fetch(`/api/entries/${encodeURIComponent(key)}`, { method: 'DELETE' });
    return res.json();
  },
  async setLocales(locales) {
    const res = await fetch('/api/locales', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ locales })
    });
    return res.json();
  },
  async exportAll() {
    const res = await fetch('/api/export');
    return res.json();
  },
  async importAll(payload) {
    const res = await fetch('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return res.json();
  }
};

const state = {
  locales: [],
  entries: {}
};

const tableRoot = document.getElementById('tableRoot');
const newKeyInput = document.getElementById('newKey');
const addKeyBtn = document.getElementById('addKeyBtn');
const newLocaleInput = document.getElementById('newLocale');
const addLocaleBtn = document.getElementById('addLocaleBtn');
const searchInput = document.getElementById('searchInput');
const exportBtn = document.getElementById('exportBtn');
const importInput = document.getElementById('importInput');

function buildTable() {
  const locales = state.locales;
  const keys = Object.keys(state.entries).sort();
  const columns = 2 + locales.length;
  const grid = document.createElement('div');
  grid.className = 'grid';
  grid.style.gridTemplateColumns = `220px repeat(${locales.length}, minmax(160px, 1fr)) 120px`;

  grid.appendChild(cell('Key', 'header key'));
  locales.forEach(locale => grid.appendChild(cell(locale, 'header')));
  grid.appendChild(cell('操作', 'header'));

  keys.forEach(key => {
    grid.appendChild(cell(key, 'key'));
    locales.forEach(locale => {
      const value = (state.entries[key] || {})[locale] || '';
      const c = cell(value, 'editable');
      c.contentEditable = 'true';
      c.dataset.key = key;
      c.dataset.locale = locale;
      c.addEventListener('blur', onCellBlur);
      grid.appendChild(c);
    });
    const actionCell = document.createElement('div');
    actionCell.className = 'cell';
    const delBtn = document.createElement('button');
    delBtn.className = 'action-btn';
    delBtn.textContent = '删除';
    delBtn.addEventListener('click', () => deleteKey(key));
    actionCell.appendChild(delBtn);
    grid.appendChild(actionCell);
  });

  tableRoot.innerHTML = '';
  tableRoot.appendChild(grid);
}

function cell(text, extraClass = '') {
  const div = document.createElement('div');
  div.className = `cell ${extraClass}`.trim();
  div.textContent = text;
  return div;
}

async function refresh(search = '') {
  const [locales, entries] = await Promise.all([
    api.getLocales(),
    api.getEntries(search)
  ]);
  state.locales = locales.locales || [];
  state.entries = entries.entries || {};
  buildTable();
}

async function addKey() {
  const key = newKeyInput.value.trim();
  if (!key) return;
  await api.addKey(key);
  newKeyInput.value = '';
  await refresh(searchInput.value.trim());
}

async function addLocale() {
  const locale = newLocaleInput.value.trim();
  if (!locale) return;
  const locales = [...state.locales, locale];
  await api.setLocales(locales);
  newLocaleInput.value = '';
  await refresh(searchInput.value.trim());
}

async function onCellBlur(event) {
  const key = event.target.dataset.key;
  const locale = event.target.dataset.locale;
  const value = event.target.textContent || '';
  await api.updateEntry(key, { [locale]: value });
}

async function deleteKey(key) {
  await api.deleteEntry(key);
  await refresh(searchInput.value.trim());
}

async function onExport() {
  const payload = await api.exportAll();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'i18n_store.json';
  a.click();
  URL.revokeObjectURL(url);
}

async function onImport(event) {
  const file = event.target.files[0];
  if (!file) return;
  const text = await file.text();
  const payload = JSON.parse(text);
  await api.importAll(payload);
  await refresh(searchInput.value.trim());
  event.target.value = '';
}

addKeyBtn.addEventListener('click', addKey);
addLocaleBtn.addEventListener('click', addLocale);
searchInput.addEventListener('input', () => refresh(searchInput.value.trim()));
exportBtn.addEventListener('click', onExport);
importInput.addEventListener('change', onImport);

refresh();

const $ = s => document.querySelector(s);
let SEM = {ua: [], ru: []};
const FIELDS = ['domain','title','url_ru','url_ua','facts','notes'];

// запам'ятовуємо дані проєкту в браузері
FIELDS.forEach(id => { const el = $('#'+id); try { el.value = localStorage.getItem('tz_'+id) || ''; } catch(e){} el.addEventListener('input', () => { try { localStorage.setItem('tz_'+id, el.value); } catch(e){} }); });

fetch('/api/health').then(r => r.json()).then(h => {
  const miss = [];
  if (!h.mock) { if (!h.anthropic_key) miss.push('ANTHROPIC_API_KEY'); if (!h.serper_key) miss.push('SERPER_API_KEY'); }
  $('#health').textContent = miss.length ? '⚠ не задано: ' + miss.join(', ') : 'модель: ' + h.model;
  $('#dfsHint').textContent = (h.dataforseo || h.mock) ? 'Частотність: Google Ads по Україні (DataForSEO).' : '⚠ DataForSEO не підключено — ключі зберуться, але без частотності.';
});

document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
  ['auto','file','text'].forEach(n => $('#tab-'+n).classList.toggle('hidden', t.dataset.tab !== n));
  $('#parseRow').classList.toggle('hidden', t.dataset.tab === 'auto');
});

const drop = $('#drop'), fileIn = $('#file');
fileIn.onchange = () => $('#fname').textContent = fileIn.files[0]?.name || 'Оберіть файл';
['dragover','dragenter'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', ev => { fileIn.files = ev.dataTransfer.files; fileIn.onchange(); });

$('#parseBtn').onclick = async () => {
  const fd = new FormData();
  if (fileIn.files[0]) fd.append('file', fileIn.files[0]);
  fd.append('text_ua', $('#text_ua').value); fd.append('text_ru', $('#text_ru').value);
  $('#parseMsg').textContent = 'Розбираю…';
  const r = await fetch('/api/parse', {method: 'POST', body: fd});
  const d = await r.json().catch(() => ({detail: `Помилка сервера (HTTP ${r.status})`}));
  if (!r.ok) { $('#parseMsg').textContent = d.detail || 'Помилка'; return; }
  SEM = {ua: d.ua, ru: d.ru};
  $('#parseMsg').textContent = `Знайдено кластерів: UA — ${d.ua.length}, RU — ${d.ru.length}`;
  renderPages(d.pages);
};

function opts(lang, sel) {
  let h = '<option value="">— немає —</option>';
  SEM[lang].forEach((c, i) => h += `<option value="${i}" ${i === sel ? 'selected' : ''}>${esc(c.name)} (${c.keywords.length})</option>`);
  return h;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sumV = c => c ? c.keywords.reduce((a, k) => a + (+k[1] || 0), 0) : 0;

function kwPreview(tr) {
  const u = SEM.ua[tr.querySelector('.selua').value], r = SEM.ru[tr.querySelector('.selru').value];
  const f = (c, l) => c ? `<b>${l}</b> ${c.keywords.slice(0,3).map(k => esc(k[0])).join(', ')}${c.keywords.length > 3 ? '…' : ''} <i>(Σ ${sumV(c)})</i>` : '';
  const miss = [u, r].filter(Boolean).reduce((a, c) => a + c.keywords.filter(k => k[1] === null || k[1] === undefined).length, 0);
  tr.querySelector('.kws').innerHTML = ([f(u,'UA'), f(r,'RU')].filter(Boolean).join('<br>') || '<span class="err">немає ключів</span>')
    + (miss ? `<br><span class="tag">без частотності: ${miss} — дозніметься через DataForSEO</span>` : '');
}

function addRow(p) {
  const tb = $('#pagesTbl tbody'), tr = document.createElement('tr');
  const extra = p.main === false;
  tr.innerHTML = `<td><input type="checkbox" class="inc" ${extra ? '' : 'checked'}></td><td class="num"></td>
    <td><select class="selua">${opts('ua', p.ua)}</select></td><td><select class="selru">${opts('ru', p.ru)}</select></td>
    <td class="kws" title="Натисніть, щоб редагувати ключі"></td><td><input class="pnote" placeholder="напр. акцент на B2B">${extra ? `<div class="muted small">Запропонована дод. сторінка${p.why ? ': ' + esc(p.why) : ''}</div>` : ''}</td>`;
  tb.appendChild(tr);
  tr.querySelectorAll('select').forEach(s => s.onchange = () => kwPreview(tr));
  tr.querySelector('.kws').onclick = () => editKw(tr);
  kwPreview(tr); renum();
  return tr;
}
let EDIT_TR = null;
const toText = c => c ? c.keywords.map(k => k[1] === null || k[1] === undefined ? k[0] : `${k[0]}; ${k[1]}`).join('\n') : '';
const fromText = t => t.split('\n').map(l => l.trim()).filter(Boolean).map(l => { const p = l.split(/\s*[;|\t]\s*/); const n = parseInt(p[1]); return [p[0], Number.isNaN(n) ? null : n]; });
function editKw(tr) {
  EDIT_TR = tr;
  const u = SEM.ua[tr.querySelector('.selua').value], r = SEM.ru[tr.querySelector('.selru').value];
  $('#kwNameUa').value = u?.name || ''; $('#kwNameRu').value = r?.name || '';
  $('#kwUa').value = toText(u); $('#kwRu').value = toText(r);
  $('#kwDlg').showModal();
}
$('#kwSave').onclick = () => {
  const tr = EDIT_TR;
  [['ua', '.selua', '#kwUa', '#kwNameUa'], ['ru', '.selru', '#kwRu', '#kwNameRu']].forEach(([l, sel, ta, nm]) => {
    const kws = fromText($(ta).value), s = tr.querySelector(sel);
    let c = SEM[l][s.value];
    if (!kws.length) { s.value = ''; return; }
    if (!c) { c = {name: $(nm).value || 'Нова сторінка', keywords: []}; SEM[l].push(c); }
    c.name = $(nm).value || c.name; c.keywords = kws.sort((a, b) => (b[1] || 0) - (a[1] || 0));
    const idx = SEM[l].indexOf(c);
    document.querySelectorAll('#pagesTbl ' + sel).forEach(x => { const v = x.value; x.innerHTML = opts(l, v === '' ? null : +v); });
    s.value = idx;
  });
  kwPreview(tr); $('#kwDlg').close();
};
function renum() { document.querySelectorAll('#pagesTbl tbody tr').forEach((tr, i) => tr.querySelector('.num').textContent = i + 1); }
function renderPages(pages) {
  $('#pagesTbl tbody').innerHTML = '';
  pages.forEach(addRow);
  $('#pagesCard').classList.remove('hidden'); $('#genCard').classList.remove('hidden');
}
$('#addPage').onclick = () => { const tr = addRow({ua: null, ru: null}); };

$('#collectBtn').onclick = async () => {
  const entries = $('#entries').value.split('\n').map(x => x.trim()).filter(Boolean);
  const langs = [...document.querySelectorAll('.lang:checked')].map(x => x.value);
  if (!entries.length) { $('#collectMsg').textContent = 'Вкажіть послугу або URL'; return; }
  const project = {domain: $('#domain').value.trim(), notes: $('#notes').value.trim()};
  $('#collectBtn').disabled = true; $('#collectMsg').textContent = 'Запускаю…';
  try {
    const r = await fetch('/api/semantics', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entries, langs, project})});
    const d = await r.json().catch(() => ({detail: `Помилка сервера (HTTP ${r.status})`}));
    $('#collectMsg').textContent = r.ok ? 'Збір запущено — прогрес праворуч. Коли буде готово, натисніть «Взяти в роботу».' : (d.detail || 'Помилка');
  } catch (e) { $('#collectMsg').textContent = 'Немає зв’язку з сервером: ' + e.message; }
  $('#collectBtn').disabled = false;
  loadJobs();
};
async function useSemantics(id) {
  const r = await fetch(`/api/jobs/${id}/semantics`); const d = await r.json();
  if (!r.ok) { alert(d.detail || 'Помилка'); return; }
  SEM = {ua: d.ua, ru: d.ru};
  renderPages(d.pages);
  const notes = (d.notes || []).filter(n => n.notes).map(n => `${n.entry}: ${n.notes}`).join('\n');
  let box = $('#semNotes'); if (!box) { box = document.createElement('div'); box.id = 'semNotes'; box.className = 'notes'; $('#pagesCard h2').after(box); }
  box.textContent = notes ? 'Коментар до семантики: ' + notes : ''; box.classList.toggle('hidden', !notes);
  $('#pagesCard').scrollIntoView({behavior: 'smooth'});
}
$('#checkAll').onchange = e => document.querySelectorAll('.inc').forEach(c => c.checked = e.target.checked);

$('#genBtn').onclick = async () => {
  const pages = [];
  document.querySelectorAll('#pagesTbl tbody tr').forEach(tr => {
    if (!tr.querySelector('.inc').checked) return;
    const u = SEM.ua[tr.querySelector('.selua').value], r = SEM.ru[tr.querySelector('.selru').value];
    if (!u && !r) return;
    pages.push({name_ua: u?.name || '', name_ru: r?.name || '', ua: u?.keywords || [], ru: r?.keywords || [], notes: tr.querySelector('.pnote').value});
  });
  const formats = [...document.querySelectorAll('.fmt input:checked')].map(x => x.value);
  if (!pages.length) { $('#genMsg').textContent = 'Оберіть хоча б одну сторінку'; return; }
  if (!formats.length) { $('#genMsg').textContent = 'Оберіть формат: Advanced або Base'; return; }
  const project = {domain: $('#domain').value.trim(), url_ru_pattern: $('#url_ru').value.trim(), url_ua_pattern: $('#url_ua').value.trim(), facts: $('#facts').value.trim(), notes: $('#notes').value.trim()};
  $('#genBtn').disabled = true; $('#genMsg').textContent = 'Запускаю…';
  try {
    const r = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: $('#title').value.trim(), project, pages, formats})});
    const d = await r.json().catch(() => ({detail: `Помилка сервера (HTTP ${r.status})`}));
    $('#genMsg').textContent = r.ok ? `Задачу створено (${pages.length} стор.). Прогрес — праворуч.` : (d.detail || 'Помилка');
  } catch (e) { $('#genMsg').textContent = 'Немає зв’язку з сервером: ' + e.message; }
  $('#genBtn').disabled = false;
  loadJobs();
};

const ST = {queued: 'в черзі', running: 'генерується', done: 'готово', partial: 'частково', error: 'помилка'};
async function loadJobs() {
  const r = await fetch('/api/jobs'); if (!r.ok) return;
  const list = await r.json();
  if (!list.length) { $('#jobs').innerHTML = '<p class="muted small">Поки порожньо</p>'; return; }
  $('#jobs').innerHTML = list.map(j => `<div class="job">
    <div class="job-head"><span class="job-title">${esc(j.title)}</span><span class="status ${j.status}">${ST[j.status] || j.status}</span></div>
    <div class="muted small">${new Date(j.created * 1000).toLocaleString('uk-UA')}</div>
    <div class="bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div>
    <div class="files">${(j.files || []).map(f => `<a href="/api/jobs/${j.id}/files/${encodeURIComponent(f)}">⬇ ${esc(f)}</a>`).join('')}</div>
    ${j.error ? `<div class="err">${esc(j.error)}</div>` : ''}
    ${j.kind === 'semantics' && (j.status === 'done' || j.status === 'partial') ? `<button class="btn small" onclick="useSemantics('${j.id}')">Взяти в роботу</button>` : ''}
    <button class="btn ghost small" onclick="showLog('${j.id}')">Лог</button>
    <button class="btn ghost small" onclick="delJob('${j.id}')">Видалити</button></div>`).join('');
}
async function showLog(id) {
  const j = await (await fetch('/api/jobs/' + id)).json();
  $('#logTitle').textContent = j.title;
  $('#logBody').textContent = (j.log || []).map(l => `${l.t}  ${l.m}`).join('\n');
  $('#logDlg').showModal();
}
async function delJob(id) { if (!confirm('Видалити задачу зі списку?')) return; await fetch('/api/jobs/' + id, {method: 'DELETE'}); loadJobs(); }
loadJobs(); setInterval(loadJobs, 4000);

$('#diagBtn').onclick = async () => {
  const box = $('#diagOut'); box.classList.remove('hidden'); box.textContent = 'Перевіряю…';
  try {
    const d = (await (await fetch('/api/diag')).json()).dataforseo;
    const lines = [];
    lines.push('Змінні задані: ' + (d.configured ? 'так' : 'ні') + (d.login ? ` (логін ${d.login})` : ''));
    if (d.auth_status) lines.push('Авторизація: ' + d.auth_status + (d.auth_http ? ` [HTTP ${d.auth_http}]` : ''));
    if (d.balance !== undefined && d.balance !== null) lines.push('Баланс: $' + d.balance);
    if (d.test_volume) lines.push('Тест «seo просування» (UA): ' + JSON.stringify(d.test_volume));
    if (d.error) lines.push('ПОМИЛКА: ' + d.error);
    box.textContent = lines.join('\n');
  } catch (e) { box.textContent = 'Не вдалося перевірити: ' + e.message; }
};

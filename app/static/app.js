const $ = s => document.querySelector(s);
let SEM = {ua: [], ru: []};
const FIELDS = ['domain','title','url_ru','url_ua','facts','notes'];

// запам'ятовуємо дані проєкту в браузері
FIELDS.forEach(id => { const el = $('#'+id); try { el.value = localStorage.getItem('tz_'+id) || ''; } catch(e){} el.addEventListener('input', () => { try { localStorage.setItem('tz_'+id, el.value); } catch(e){} }); });

fetch('/api/health').then(r => r.json()).then(h => {
  const miss = [];
  if (!h.mock) { if (!h.anthropic_key) miss.push('ANTHROPIC_API_KEY'); if (!h.serper_key) miss.push('SERPER_API_KEY'); }
  $('#health').textContent = miss.length ? '⚠ не задано: ' + miss.join(', ') : 'модель: ' + h.model;
});

document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
  $('#tab-file').classList.toggle('hidden', t.dataset.tab !== 'file');
  $('#tab-text').classList.toggle('hidden', t.dataset.tab !== 'text');
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
  const d = await r.json();
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
  tr.querySelector('.kws').innerHTML = [f(u,'UA'), f(r,'RU')].filter(Boolean).join('<br>') || '<span class="err">немає ключів</span>';
}

function addRow(p) {
  const tb = $('#pagesTbl tbody'), tr = document.createElement('tr');
  tr.innerHTML = `<td><input type="checkbox" class="inc" checked></td><td class="num"></td>
    <td><select class="selua">${opts('ua', p.ua)}</select></td><td><select class="selru">${opts('ru', p.ru)}</select></td>
    <td class="kws"></td><td><input class="pnote" placeholder="напр. акцент на B2B"></td>`;
  tb.appendChild(tr);
  tr.querySelectorAll('select').forEach(s => s.onchange = () => kwPreview(tr));
  kwPreview(tr); renum();
}
function renum() { document.querySelectorAll('#pagesTbl tbody tr').forEach((tr, i) => tr.querySelector('.num').textContent = i + 1); }
function renderPages(pages) {
  $('#pagesTbl tbody').innerHTML = '';
  pages.forEach(addRow);
  $('#pagesCard').classList.remove('hidden'); $('#genCard').classList.remove('hidden');
}
$('#addPage').onclick = () => addRow({ua: null, ru: null});
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
  const r = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: $('#title').value.trim(), project, pages, formats})});
  const d = await r.json();
  $('#genBtn').disabled = false;
  $('#genMsg').textContent = r.ok ? `Задачу створено (${pages.length} стор.). Прогрес — праворуч.` : (d.detail || 'Помилка');
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

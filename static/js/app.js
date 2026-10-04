const $ = id => document.getElementById(id);
const chat = $('chat'), form = $('chatForm'), question = $('question'), sourceUsed = $('sourceUsed');
const stages = [...document.querySelectorAll('#pipeline li')];
const MARK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 5v14M5 12h5c3 0 3-6 6-6h3M10 12c3 0 3 6 6 6h3"/></svg>';
const esc = (s = '') => String(s).replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const inline = s => esc(s).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');

function format(s = '') {
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of s.split('\n')) {
    const ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), m = ul || ol;
    if (m) { const t = ul ? 'ul' : 'ol'; if (list !== t) { close(); out.push(`<${t}>`); list = t; } out.push(`<li>${inline(m[1])}</li>`); }
    else { close(); if (line.trim()) out.push(`<p>${inline(line)}</p>`); }
  }
  close(); return out.join('');
}

/* Pipeline: live progress while waiting, then the real path from the trace */
const patterns = { route: /rout/i, retrieve: /retriev|private|knowledge|\bkb\b/i, grade: /grad|evidence|relevan/i, web: /web|tavily|search/i, rewrite: /rewrit|retry/i };
let timer;
const setStages = fn => stages.forEach(li => { li.className = fn(li.dataset.k) || ''; });
function startProgress() {
  clearInterval(timer); let i = 0;
  const tick = () => setStages(k => { const n = stages.findIndex(l => l.dataset.k === k); return n < i ? 'done' : n === i ? 'active' : ''; });
  tick(); timer = setInterval(() => { if (i < 2) { i++; tick(); } }, 900);
}
function finishProgress(trace = []) {
  clearInterval(timer);
  const text = trace.join(' | ');
  setStages(k => k === 'answer' || patterns[k]?.test(text) ? 'done' : 'skipped');
}
function resetProgress() { clearInterval(timer); setStages(() => ''); }

const chipClass = s => /web/i.test(s) ? 'web' : s ? 'kb' : '';

function addTurn(q) {
  $('hero')?.remove();
  const t = document.createElement('div'); t.className = 'turn';
  t.innerHTML = `<div class="q"><p>${esc(q)}</p></div><div class="a"><div class="a-mark">${MARK}</div><div class="a-body"><div class="thinking"><i></i>Searching company knowledge...</div></div></div>`;
  chat.appendChild(t); chat.scrollTop = chat.scrollHeight;
  return t.querySelector('.a-body');
}

function fillAnswer(el, d) {
  const cites = (d.citations || []).map((c, i) => c.url
    ? `<a class="cite" href="${esc(c.url)}" target="_blank" rel="noopener"><b>${i + 1}</b><span>${esc(c.title)}</span></a>`
    : `<div class="cite"><b>${i + 1}</b><span>${esc(c.title)}</span></div>`).join('');
  const steps = (d.trace || []).length ? `<details class="steps"><summary>How this was answered</summary><ol>${d.trace.map(x => `<li>${esc(x)}</li>`).join('')}</ol></details>` : '';
  el.innerHTML = `${format(d.answer)}<div class="meta"><span class="chip ${chipClass(d.source_used)}">${esc(d.source_used || 'Unknown')}</span><button class="mbtn" type="button">Copy</button></div>${cites ? `<div class="cites">${cites}</div>` : ''}${steps}`;
  const b = el.querySelector('.mbtn');
  b.onclick = async () => { await navigator.clipboard.writeText(d.answer); b.textContent = 'Copied'; setTimeout(() => b.textContent = 'Copy', 1400); };
}

async function ask(q) {
  question.value = ''; autosize();
  const el = addTurn(q), btn = form.querySelector('.send');
  btn.disabled = true; sourceUsed.textContent = 'Working'; startProgress();
  try {
    const res = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) });
    const d = await res.json(); if (!res.ok) throw new Error(d.detail || 'Request failed');
    fillAnswer(el, d); finishProgress(d.trace); sourceUsed.textContent = d.source_used;
  } catch (e) {
    el.classList.add('err'); el.textContent = `${e.message}. Check your connection and try again.`;
    resetProgress(); sourceUsed.textContent = 'None';
  } finally { btn.disabled = false; question.focus(); chat.scrollTop = chat.scrollHeight; }
}

function autosize() { question.style.height = 'auto'; question.style.height = Math.min(question.scrollHeight, 170) + 'px'; }
question.addEventListener('input', autosize);
question.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
form.addEventListener('submit', e => { e.preventDefault(); const q = question.value.trim(); if (q) ask(q); });
document.querySelectorAll('.example').forEach(b => b.addEventListener('click', () => ask(b.querySelector('span').textContent.trim())));
$('newChat').onclick = () => location.reload();

/* Upload */
const modal = $('uploadModal'), status = $('uploadStatus');
const closeModal = () => modal.classList.add('hidden');
$('openUpload').onclick = () => modal.classList.remove('hidden');
$('closeUpload').onclick = closeModal;
modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
$('fileInput').onchange = e => { $('fileName').textContent = e.target.files[0]?.name || 'Choose a file'; };
$('uploadBtn').onclick = async () => {
  const file = $('fileInput').files[0]; status.className = 'upload-status';
  if (!file) { status.textContent = 'Choose a file to index.'; status.classList.add('error'); return; }
  status.textContent = 'Indexing document...';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/api/ingest', { method: 'POST', headers: { 'X-Admin-Key': $('adminKey').value }, body: fd });
    const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'Upload failed');
    status.textContent = `Indexed ${d.file} (${d.chunks} chunks).`;
  } catch (e) { status.textContent = `${e.message}. Check the admin key and file type.`; status.classList.add('error'); }
};
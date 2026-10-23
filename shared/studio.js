/* Shared behaviour for both studios: help toggles, the guide, sliders that speak plain English, status. */
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* "How does this work?" buttons open the explainer named in data-explain. */
document.querySelectorAll('[data-explain]').forEach(b => {
  const box = $(b.dataset.explain);
  b.setAttribute('aria-controls', box.id); b.setAttribute('aria-expanded', 'false'); box.hidden = true;
  b.addEventListener('click', () => {
    const open = box.hidden; box.hidden = !open;
    b.setAttribute('aria-expanded', String(open));
    b.textContent = open ? 'Hide the explanation' : b.dataset.label;
  });
  b.dataset.label = b.textContent;
});

/* The guide opens on the first visit, then only from the Guide button. */
const guide = $('guide');
function openGuide(){ guide.showModal(); }
$('guide-open').addEventListener('click', openGuide);
$('guide-close').addEventListener('click', () => guide.close());
try { if (!localStorage.getItem(STUDIO_KEY + ':seen-guide')) { openGuide(); localStorage.setItem(STUDIO_KEY + ':seen-guide', '1'); } } catch (e) {}

/* A slider shows its value in words. words(v) returns the text for the current value. */
function slider(id, value, words){
  const r = $(id), out = $(id + '_now');
  r.value = value;
  const show = () => { out.textContent = words(+r.value); };
  r.addEventListener('input', show); show();
  return r;
}
const band = (v, lo, hi, a, b, c) => v < lo ? a : v > hi ? c : b;
const secs = v => v === 0 ? 'no pause' : (v === 1 ? '1 second' : v + ' seconds');

function setStatus(text, kind){ const s = $('status'); s.textContent = text; s.className = 'status' + (kind ? ' ' + kind : ''); }
function onAir(on){ const o = $('onair'); o.classList.toggle('live', on); $('onair-text').textContent = on ? 'Creating audio…' : 'Ready'; }

/* Script line count: each non-empty line is spoken as its own piece. */
function countLines(){
  const n = $('text').value.split('\n').filter(l => l.trim()).length;
  $('script-meta').textContent = n === 1 ? '1 line, spoken as 1 piece' : `${n} lines, spoken as ${n} separate pieces`;
}
$('text').addEventListener('input', countLines);

/* Shared render / save / remember flow. The page supplies settings() and the endpoints match both servers. */
let LAST = null;
async function create(selectionOnly){
  const ta = $('text');
  let t = ta.value;
  if (selectionOnly){
    t = ta.value.substring(ta.selectionStart, ta.selectionEnd);
    if (!t.trim()) { setStatus('Select one or more lines in the script first, then try again.', 'bad'); return; }
  }
  if (!t.trim()) { setStatus('The script is empty. Type or paste what you want spoken.', 'bad'); return; }
  onAir(true);
  setStatus(STATUS.loaded || STATUS.preview ? 'Creating audio. This takes about as long as the audio itself.' : FIRST_LOAD_TEXT);
  const t0 = Date.now();
  try {
    const r = await fetch('/render', {method:'POST', body:JSON.stringify({preset:settings(), text:t})});
    if (!r.ok) { setStatus(await r.text(), 'bad'); return; }
    STATUS.loaded = true;
    if (window.afterRender) afterRender(r);
    const lines = decodeURIComponent(r.headers.get('X-Spoken') || '').split('\n').filter(Boolean);
    $('read').innerHTML = lines.map(l => `<li>${esc(l)}</li>`).join('');
    $('read').hidden = false; $('read-empty').hidden = true;
    LAST = r.headers.get('X-Render-Id'); $('save').disabled = false;
    const b = await r.blob(); $('audio').src = URL.createObjectURL(b); $('audio').play().catch(() => {});
    setStatus(`Audio ready in ${((Date.now() - t0) / 1000).toFixed(1)} seconds. Press play to hear it again.`, 'ok');
  } catch (e) {
    setStatus('The studio stopped responding. Check the window it is running in, then reload this page.', 'bad');
  } finally { onAir(false); }
}
async function saveTake(){
  if (!LAST) return;
  const r = await fetch('/export', {method:'POST', body:JSON.stringify({id:LAST, name:$('xname').value})});
  setStatus(await r.text(), r.ok ? 'ok' : 'bad');
}
async function remember(){
  const r = await fetch('/preset', {method:'POST', body:JSON.stringify(settings())});
  setStatus(r.ok ? 'Settings remembered. They will be here next time you open the studio.' : await r.text(), r.ok ? 'ok' : 'bad');
}
$('create').addEventListener('click', () => create(false));
$('create-sel').addEventListener('click', () => create(true));
$('save').addEventListener('click', saveTake);
$('remember').addEventListener('click', remember);

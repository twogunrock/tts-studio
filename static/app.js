/* TTS Studio page. Data comes from the server in STATE, PRESETS, VOICES, SAMPLES, SAMPLE (see page.html). */
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const store = {get(k){ try { return localStorage.getItem('tts-studio:' + k); } catch (e) { return null; } },
               set(k, v){ try { localStorage.setItem('tts-studio:' + k, v); } catch (e) {} }};
let ENGINE = 'kokoro', LAST = null;

/* ── small shared pieces ───────────────────────────────────────────────────── */
document.querySelectorAll('[data-explain]').forEach(b => {
  const box = $(b.dataset.explain);
  b.setAttribute('aria-controls', box.id); b.setAttribute('aria-expanded', 'false'); box.hidden = true;
  b.addEventListener('click', () => { box.hidden = !box.hidden; b.setAttribute('aria-expanded', String(!box.hidden)); });
});
function slider(id, value, words){
  const r = $(id), out = $(id + '_now');
  r.value = value;
  const show = () => { out.textContent = words(+r.value); };
  r.addEventListener('input', show); show();
}
const band = (v, lo, hi, a, b, c) => v < lo ? a : v > hi ? c : b;
const secs = v => v === 0 ? 'no pause' : (v === 1 ? '1 second' : v + ' seconds');
function setStatus(text, kind){ const s = $('status'); s.textContent = text; s.className = 'status' + (kind ? ' ' + kind : ''); }
/* The on-air light: grey when idle, red while creating or playing. */
function onAir(on, text){ $('onair').classList.toggle('live', on); $('onair-text').textContent = on ? text : 'Ready'; }
async function api(path, body){
  return fetch(path, {method:'POST', body:JSON.stringify(body || {})});
}

/* ── light / dark (the first choice is made in page.html before the page draws) ── */
function themeLabel(){
  const dark = document.documentElement.dataset.theme === 'dark';
  $('theme').setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
}
$('theme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next; store.set('theme', next); themeLabel(); drawWave();
});
themeLabel();

/* ── guide ─────────────────────────────────────────────────────────────────── */
$('guide-open').addEventListener('click', () => $('guide').showModal());
$('guide-close').addEventListener('click', () => $('guide').close());
if (!store.get('seen-guide')) { $('guide').showModal(); store.set('seen-guide', '1'); }

/* ── banner (preview mode, missing pieces) ─────────────────────────────────── */
function banner(){
  let h = '';
  if (STOPPED) h = '<b>TTS Studio has stopped.</b> It stops by itself after its page is closed. To keep working, double-click the start file in the TTS Studio folder, then reload this page.';
  else if (STATE.preview) h = '<b>Preview mode.</b> No voice model is loaded, so Create audio plays a short tone for each line instead of speech. Everything else works: try the controls, and check the pacing and the cleaned-up text.';
  else if (ENGINE === 'chatterbox' && STATE.chatterbox.watermark === false)
    h = '<b>Watermark is off.</b> Chatterbox\'s watermark didn\'t load, so these clips are not marked as AI-generated. In the Python that runs Chatterbox, run <code>pip install "setuptools&lt;81"</code>, then restart the studio.';
  $('banner').innerHTML = h ? `<div class="note">${h}</div>` : '';
}

/* ── keep the studio running while this page is open; it stops a little after the last tab closes ── */
const TAB = Math.random().toString(36).slice(2);
let STOPPED = false;
function ping(){
  fetch('/api/ping?tab=' + TAB).then(r => { if (STOPPED && r.ok) { STOPPED = false; banner(); } })
    .catch(() => { if (!STOPPED) { STOPPED = true; banner(); } });
}
setInterval(ping, 15000);
addEventListener('pagehide', () => navigator.sendBeacon('/api/bye?tab=' + TAB));
addEventListener('pageshow', ev => { if (ev.persisted) ping(); });  // back from the browser's page cache
document.addEventListener('visibilitychange', () => { if (!document.hidden) ping(); });

/* ── settings drawer (phones) ──────────────────────────────────────────────── */
function drawer(open){
  $('rail').classList.toggle('open', open); $('scrim').hidden = !open;
  $('rail-open').setAttribute('aria-expanded', String(open));
  if (open) $('rail-close').focus(); else if (matchMedia('(max-width:900px)').matches) $('rail-open').focus();
}
$('rail-open').addEventListener('click', () => drawer(true));
$('rail-close').addEventListener('click', () => drawer(false));
$('scrim').addEventListener('click', () => drawer(false));
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && $('rail').classList.contains('open')) drawer(false); });
function summary(){
  let s;
  if (ENGINE === 'kokoro') {
    const names = [...$('voices').querySelectorAll('select')].map(x => voiceLabel(x.value).split(' (')[0]);
    s = `${names.join(', ')} · ${(+$('k_speed').value).toFixed(2)}×`;
  } else if (!STATE.chatterbox.python) s = 'Chatterbox isn\'t set up yet';
  else {
    const style = [...$('styles').children].find(b => b.getAttribute('aria-pressed') === 'true');
    s = `${$('c_ref').selectedOptions[0]?.textContent || ''} · ${style ? style.textContent : 'Custom style'}`;
  }
  $('rail-sum').textContent = s;
}
$('rail').addEventListener('input', summary); $('rail').addEventListener('change', summary);

/* ── engines ───────────────────────────────────────────────────────────────── */
function showEngine(e){
  ENGINE = e; store.set('engine', e);
  ['kokoro', 'chatterbox'].forEach(x => {
    $('tab-' + x).setAttribute('aria-selected', String(x === e));
    $('tab-' + x).tabIndex = x === e ? 0 : -1;
    $('engine-' + x).hidden = x !== e;
  });
  showChatterbox(); refreshCreate(); banner(); summary();
}
['kokoro', 'chatterbox'].forEach(x => $('tab-' + x).addEventListener('click', () => showEngine(x)));
document.querySelector('.engine').addEventListener('keydown', ev => {
  if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
  const next = ENGINE === 'kokoro' ? 'chatterbox' : 'kokoro'; showEngine(next); $('tab-' + next).focus();
});
function engineReady(){
  if (STATE.preview) return true;
  return ENGINE === 'kokoro' ? !STATE.kokoro.missing.length : !!STATE.chatterbox.python;
}
const NOT_READY = {kokoro: 'Download the voice model in Voice settings to start creating audio.',
                   chatterbox: 'Finish setting up Chatterbox (the steps are above your script), or switch back to Kokoro.'};
function refreshCreate(){
  const ok = engineReady();
  $('create').disabled = $('create-sel').disabled = !ok;
  if (!ok) setStatus(NOT_READY[ENGINE]);
  else if (Object.values(NOT_READY).includes($('status').textContent)) setStatus('');
}

/* ── Kokoro: voices, blend, download ───────────────────────────────────────── */
const ACCENTS = {a:'American', b:'British', e:'Spanish', f:'French', h:'Hindi', i:'Italian', j:'Japanese', p:'Portuguese', z:'Mandarin'};
function voiceLabel(code){
  const [pre, name] = code.split('_');
  if (!name || pre.length !== 2) return code;
  const who = pre[1] === 'f' ? 'female' : pre[1] === 'm' ? 'male' : '';
  return `${name[0].toUpperCase() + name.slice(1)} (${(ACCENTS[pre[0]] || pre[0]) + (who ? ' ' + who : '')})`;
}
function voiceOptions(sel){
  const groups = {};
  VOICES.forEach(v => { const k = v[0] === 'a' || v[0] === 'b' ? ACCENTS[v[0]] + ' English' : 'Other languages (use with text in that language)';
    (groups[k] = groups[k] || []).push(v); });
  return Object.entries(groups).map(([g, vs]) => `<optgroup label="${esc(g)}">` +
    vs.map(v => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(voiceLabel(v))}</option>`).join('') + '</optgroup>').join('');
}
const SWATCH = ['var(--knob)', '#C8901A', '#6C8EBF', '#B5739D', '#8AA05A', '#C2695B'];
function drawBlend(){
  const rows = [...$('voices').children], ws = rows.map(r => +r.querySelector('input').value), tot = ws.reduce((a, b) => a + b, 0) || 1;
  rows.forEach((r, i) => { r.querySelector('.share').textContent = Math.round(ws[i] / tot * 100) + '%'; r.querySelector('.remove').hidden = rows.length === 1; });
  $('bar').innerHTML = rows.map((r, i) => `<span style="width:${ws[i] / tot * 100}%;background:${SWATCH[i % SWATCH.length]}"></span>`).join('');
}
function voiceRow(v, w){
  const d = document.createElement('div'); d.className = 'blend-row';
  const n = $('voices').children.length + 1;
  d.innerHTML = `<select aria-label="Voice ${n}">${voiceOptions(v)}</select><span class="share"></span>
    <input type="range" min="0" max="1" step="0.05" value="${w}" aria-label="Share of voice ${n}">
    <button class="remove" type="button">Remove</button>`;
  d.querySelector('input').addEventListener('input', drawBlend);
  d.querySelector('.remove').addEventListener('click', () => { d.remove(); drawBlend(); summary(); });
  $('voices').appendChild(d);
}
$('add-voice').addEventListener('click', () => {
  const used = [...$('voices').querySelectorAll('select')].map(s => s.value);
  voiceRow(VOICES.find(v => !used.includes(v)) || VOICES[0], 0.5); drawBlend(); summary();
});
function showDownload(){
  const need = !STATE.preview && STATE.kokoro.missing.length, dl = STATE.kokoro.download;
  $('k-download').hidden = !need;
  if (!need) return;
  $('k-download-go').disabled = dl.running;
  $('k-progress').hidden = !dl.running;
  if (dl.running && dl.total) $('k-progress').firstElementChild.style.width = (dl.done / dl.total * 100) + '%';
  $('k-download-status').textContent = dl.error || (dl.running ? `Downloading… ${(dl.done / 1e6).toFixed(0)} of ${(dl.total / 1e6).toFixed(0)} MB` : '');
  $('k-download-status').className = 'status' + (dl.error ? ' bad' : '');
}
async function pollDownload(){
  const s = await (await fetch('/api/state')).json(); Object.assign(STATE, s); showDownload();
  if (s.kokoro.download.running) setTimeout(pollDownload, 700);
  else if (!s.kokoro.missing.length) location.reload();  // the real voice list arrives with the page
}
$('k-download-go').addEventListener('click', async () => { await api('/api/kokoro/download'); pollDownload(); });

/* ── Chatterbox: set-up, picker, styles ────────────────────────────────────── */
const BUILTIN = '(built-in voice)';
const STYLES = [  /* Expressive follows the Chatterbox authors' tip: more exaggeration, lower CFG */
  {name:'Steady narrator', exaggeration:0.4, cfg_weight:0.5, temperature:0.7},
  {name:'Natural', exaggeration:0.5, cfg_weight:0.5, temperature:0.8},
  {name:'Expressive', exaggeration:0.7, cfg_weight:0.3, temperature:0.8},
];
const KNOBS = ['exaggeration', 'cfg_weight', 'temperature'];
function showChatterbox(){
  const ready = !!STATE.chatterbox.python;
  $('cb-setup').hidden = ready || ENGINE !== 'chatterbox';
  $('cb-rail-setup').hidden = ready; $('cb-ready').hidden = !ready;
  if (ready) $('cb-using').textContent = STATE.chatterbox.python;
}
function describeCheck(r){
  if (!r.ok) return `<p class="bad"><b>Not ready.</b> ${esc(r.error)}</p>`;
  const gpu = r.gpu ? `Graphics card: <b>${esc(r.gpu)}</b>.` : '<b>No graphics card found.</b> It will work, but slowly (about 20 times slower than real time).';
  const wm = r.watermark ? 'Watermark: <b>on</b>.' : '<b>Watermark is off.</b> Run <code>pip install "setuptools&lt;81"</code> in that environment, then check again.';
  return `<p class="ok"><b>Found Chatterbox ${esc(r.chatterbox)}</b> (Python ${esc(r.python_version)}).</p><p>${gpu} ${wm}</p>
    <div class="row"><button class="btn" type="button" id="cb-use">Use this Chatterbox</button></div>`;
}
$('cb-check').addEventListener('click', async () => {
  const path = $('cb-path').value.trim();
  if (!path) { $('cb-result').innerHTML = '<p class="bad">Paste or browse to the folder you installed Chatterbox into first.</p>'; return; }
  $('cb-result').innerHTML = '<p>Checking. Chatterbox takes a little while to start, so give it up to a minute…</p>';
  const r = await (await api('/api/chatterbox/check', {path})).json();
  $('cb-result').innerHTML = describeCheck(r);
  if (r.ok) $('cb-use').addEventListener('click', async () => {
    const res = await api('/api/chatterbox/use', {path});
    if (res.ok) { Object.assign(STATE, await res.json()); showChatterbox(); refreshCreate(); summary();
      setStatus('Chatterbox is ready. The first time you create audio it downloads its model (about 3 GB), so allow a few minutes.', 'ok'); }
  });
});
$('cb-forget').addEventListener('click', async () => {
  Object.assign(STATE, await (await api('/api/chatterbox/forget')).json()); showChatterbox(); refreshCreate(); summary();
});
/* Folder browser: the server lists folders, because a web page can't see local paths. */
async function browseTo(path){
  const r = await (await fetch('/api/browse?path=' + encodeURIComponent(path || ''))).json();
  $('browser').dataset.path = r.path; $('b-path').textContent = r.path;
  $('b-drives').innerHTML = (r.drives || []).map(d => `<button class="chip" type="button" data-go="${esc(d)}">${esc(d)}</button>`).join('');
  const items = [];
  if (r.parent) items.push(`<li><button type="button" data-go="${esc(r.parent)}">Up one level</button></li>`);
  r.dirs.forEach(d => items.push(`<li><button type="button" data-go="${esc(d.path)}">${esc(d.name)}${d.env ? ' <span class="tag">Python environment</span>' : ''}</button></li>`));
  if (r.error) items.push(`<li class="bad">${esc(r.error)}</li>`);
  if (!r.dirs.length && !r.error) items.push('<li class="hint">No folders in here.</li>');
  $('b-list').innerHTML = items.join('');
  $('b-pick').textContent = r.here_env ? 'Use this folder (Python environment)' : 'Use this folder';
}
$('browser').addEventListener('click', ev => { const b = ev.target.closest('[data-go]'); if (b) browseTo(b.dataset.go); });
$('cb-browse').addEventListener('click', () => { browseTo($('cb-path').value.trim()); $('browser').showModal(); });
$('b-cancel').addEventListener('click', () => $('browser').close());
$('b-pick').addEventListener('click', () => { $('cb-path').value = $('browser').dataset.path; $('browser').close(); $('cb-check').click(); });

function markStyle(){
  [...$('styles').children].forEach((b, i) => b.setAttribute('aria-pressed',
    String(KNOBS.every(k => Math.abs(+$('c_' + k).value - STYLES[i][k]) < 0.001))));
}
STYLES.forEach(s => {
  const b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.textContent = s.name;
  b.addEventListener('click', () => { KNOBS.forEach(k => { $('c_' + k).value = s[k]; $('c_' + k).dispatchEvent(new Event('input', {bubbles: true})); }); });
  $('styles').appendChild(b);
});
$('new-take').addEventListener('click', () => { $('c_seed').value = Math.floor(Math.random() * 99999); setStatus('New take number set. Create audio to hear it.'); });

/* ── settings per engine ───────────────────────────────────────────────────── */
function settings(){
  if (ENGINE === 'kokoro') return {voices: [...$('voices').children].map(d => [d.querySelector('select').value, +d.querySelector('input').value]),
    speed: +$('k_speed').value, gap_line: +$('k_gap_line').value, gap_head: +$('k_gap_head').value, fixes: $('k_fixes').checked};
  return {ref: $('c_ref').value, exaggeration: +$('c_exaggeration').value, cfg_weight: +$('c_cfg_weight').value,
    temperature: +$('c_temperature').value, seed: +$('c_seed').value, gap_line: +$('c_gap_line').value,
    gap_head: +$('c_gap_head').value, fixes: $('c_fixes').checked};
}

/* ── the script: a textarea over a mirror that numbers lines and lights the one being spoken ── */
function drawMirror(){
  const ta = $('text');
  $('mirror').innerHTML = ta.value.split('\n').map(l => `<div class="ml${l.trim() ? ' n' : ''}">${esc(l) || '​'}</div>`).join('');
  ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px';  // grow with the text, so the page scrolls instead of the box
  const n = ta.value.split('\n').filter(l => l.trim()).length;
  $('script-meta').textContent = n === 1 ? '1 line, spoken as 1 piece' : `${n} lines, spoken as ${n} separate pieces`;
  lightLine(-1);
}
$('text').addEventListener('input', drawMirror);
window.addEventListener('resize', () => { drawMirror(); drawWave(); });

/* ── create, save, remember ────────────────────────────────────────────────── */
/* TAKE: what the current audio was made from. Lines light up only while the script still matches it. */
let TAKE = null;
async function create(selectionOnly){
  const ta = $('text');
  let t = ta.value, offset = 0;
  if (selectionOnly){
    t = ta.value.substring(ta.selectionStart, ta.selectionEnd);
    if (!t.trim()) { setStatus('Select one or more lines in the script first, then try again.', 'bad'); return; }
    offset = ta.value.substring(0, ta.selectionStart).split('\n').length - 1;
  }
  if (!t.trim()) { setStatus('The script is empty. Type or paste what you want spoken.', 'bad'); return; }
  const first = !STATE.preview && (ENGINE === 'kokoro' ? !STATE.kokoro.loaded : !STATE.chatterbox.running);
  const name = ENGINE === 'kokoro' ? 'Kokoro' : 'Chatterbox';
  stopPlaying();
  onAir(true, 'Creating…');
  setStatus(first ? (ENGINE === 'kokoro' ? 'Loading Kokoro, then creating audio. The first run takes a few extra seconds.'
                                         : 'Starting Chatterbox, then creating audio. The very first time it downloads its model (about 3 GB), so this can take several minutes.')
                  : 'Creating audio. With Kokoro that takes seconds; with Chatterbox, about as long as the audio itself on a graphics card.');
  const t0 = Date.now();
  $('create').disabled = $('create-sel').disabled = true;
  try {
    const r = await api('/api/render', {engine: ENGINE, preset: settings(), text: t});
    if (!r.ok) { setStatus(await r.text(), 'bad'); return; }
    if (ENGINE === 'kokoro') { STATE.kokoro.loaded = true; STATE.chatterbox.running = false; }
    else { STATE.chatterbox.running = true; STATE.kokoro.loaded = false;
      const wm = r.headers.get('X-Watermark'); if (wm) { STATE.chatterbox.watermark = wm === 'on'; banner(); } }
    const lines = decodeURIComponent(r.headers.get('X-Spoken') || '').split('\n').filter(Boolean);
    $('read').innerHTML = lines.map(l => `<li>${esc(l)}</li>`).join('');
    $('read-card').hidden = false;
    TAKE = {script: ta.value, offset, pieces: JSON.parse(decodeURIComponent(r.headers.get('X-Pieces') || '%5B%5D'))};
    LAST = r.headers.get('X-Render-Id'); $('save').disabled = false;
    const b = await r.blob();
    await loadTake(b);
    const warn = r.headers.get('X-Warning');
    setStatus(warn ? decodeURIComponent(warn) : `${name} audio ready in ${((Date.now() - t0) / 1000).toFixed(1)} seconds.`, warn ? 'bad' : 'ok');
    $('audio').play().catch(() => {});
  } catch (e) {
    setStatus('The studio stopped responding. Check the window it is running in, then reload this page.', 'bad');
  } finally { if ($('audio').paused) onAir(false); refreshCreate(); }
}
$('create').addEventListener('click', () => create(false));
$('create-sel').addEventListener('click', () => create(true));
$('save').addEventListener('click', async () => {
  if (!LAST) return;
  const r = await api('/api/save', {id: LAST, name: $('xname').value});
  setStatus(await r.text(), r.ok ? 'ok' : 'bad');
});
$('remember').addEventListener('click', async () => {
  const r = await api('/api/preset', {engine: ENGINE, preset: settings()});
  setStatus(await r.text(), r.ok ? 'ok' : 'bad');
});

/* ── player: play button, waveform you can click or drag, and the lit line ──── */
const audio = $('audio');
let PEAKS = null, RAF = 0;
const clock = s => { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
async function loadTake(blob){
  if (audio.src) URL.revokeObjectURL(audio.src);
  audio.src = URL.createObjectURL(blob);
  $('play').disabled = $('seek').disabled = false;
  PEAKS = null;
  try {  // the waveform is drawn from the take itself
    const Ctx = window.AudioContext || window.webkitAudioContext, ctx = new Ctx();
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer()); ctx.close();
    const data = buf.getChannelData(0), n = 600, step = Math.max(1, Math.floor(data.length / n));
    PEAKS = Array.from({length: n}, (_, i) => { let m = 0; for (let j = i * step; j < Math.min(data.length, (i + 1) * step); j++) m = Math.max(m, Math.abs(data[j])); return m; });
    const top = Math.max(...PEAKS) || 1; PEAKS = PEAKS.map(p => p / top);
  } catch (e) { PEAKS = null; }
  drawWave();
}
function drawWave(){
  const c = $('wave'), r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (!r.width) return;
  c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
  const g = c.getContext('2d'), css = getComputedStyle(document.documentElement);
  const on = css.getPropertyValue('--booth-ink').trim(), off = css.getPropertyValue('--booth-dim').trim();
  const bw = 3 * dpr, gap = 2 * dpr, bars = Math.floor(c.width / (bw + gap)), mid = c.height / 2;
  const done = audio.duration ? audio.currentTime / audio.duration : 0;
  g.clearRect(0, 0, c.width, c.height);
  for (let i = 0; i < bars; i++) {
    const p = PEAKS ? PEAKS[Math.floor(i / bars * PEAKS.length)] : 0.04;
    const h = Math.max(2 * dpr, p * (c.height - 4 * dpr));
    g.globalAlpha = i / bars < done ? 1 : 0.45; g.fillStyle = i / bars < done ? on : off;
    g.fillRect(i * (bw + gap), mid - h / 2, bw, h);
  }
  g.globalAlpha = 1;
}
function lightLine(piece){
  [...$('mirror').children].forEach(d => d.classList.remove('lit'));
  [...$('read').children].forEach((li, i) => li.classList.toggle('lit', i === piece));
  if (piece < 0 || !TAKE || TAKE.script !== $('text').value) return;  // script edited since: the line numbers no longer match
  const rows = $('mirror').children;
  TAKE.pieces[piece][1].forEach(i => rows[i + TAKE.offset] && rows[i + TAKE.offset].classList.add('lit'));
}
function pieceAt(t){
  if (!TAKE || !TAKE.pieces.length) return -1;
  let k = 0; TAKE.pieces.forEach((p, i) => { if (p[0] <= t + 0.01) k = i; }); return k;
}
let LIT = -2;
function tick(){
  $('time').textContent = `${clock(audio.currentTime)} / ${clock(audio.duration)}`;
  if (audio.duration) $('seek').value = Math.round(audio.currentTime / audio.duration * 1000);
  const k = audio.paused && audio.currentTime === 0 ? -1 : pieceAt(audio.currentTime);
  if (k !== LIT) { LIT = k; lightLine(k); }
  drawWave();
}
function loop(){ tick(); if (!audio.paused) RAF = requestAnimationFrame(loop); }
function stopPlaying(){ audio.pause(); }
audio.addEventListener('play', () => { $('play').classList.add('playing'); $('play').setAttribute('aria-label', 'Pause'); onAir(true, 'On air'); cancelAnimationFrame(RAF); loop(); });
audio.addEventListener('pause', () => { $('play').classList.remove('playing'); $('play').setAttribute('aria-label', 'Play'); onAir(false); tick(); });
audio.addEventListener('ended', () => { LIT = -2; lightLine(-1); });
audio.addEventListener('loadedmetadata', tick);
$('play').addEventListener('click', () => audio.paused ? audio.play().catch(() => {}) : audio.pause());
$('seek').addEventListener('input', () => { if (audio.duration) { audio.currentTime = $('seek').value / 1000 * audio.duration; tick(); } });

/* ── pronunciations: saved to overrides.json as you type ───────────────────── */
let pronTimer = null, pronBroken = false;
function pronStatus(text, kind){ const s = $('pron-status'); s.textContent = text; s.className = 'status' + (kind ? ' ' + kind : ''); }
function pronRows(){ return [...$('pron-rows').children].map(r => [r.querySelector('.p-word').value, r.querySelector('.p-say').value]); }
function pronEmpty(){ $('pron-empty').hidden = $('pron-rows').children.length > 0; }
function pronRow(word, say){
  const d = document.createElement('div'); d.className = 'pron-row';
  d.innerHTML = `<input class="p-word" type="text" aria-label="Written as" placeholder="Written as" spellcheck="false">
    <span class="arrow" aria-hidden="true">→</span>
    <input class="p-say" type="text" aria-label="Say it as" placeholder="Say it as" spellcheck="false">
    <span class="pron-actions"><button class="linkish hear" type="button">Hear it</button><button class="linkish remove" type="button">Remove</button></span>`;
  d.querySelector('.p-word').value = word; d.querySelector('.p-say').value = say;
  d.querySelectorAll('input').forEach(i => i.addEventListener('input', queuePronSave));
  d.querySelector('.remove').addEventListener('click', () => { d.remove(); pronEmpty(); queuePronSave(); });
  d.querySelector('.hear').addEventListener('click', () => hear(d));
  $('pron-rows').appendChild(d); pronEmpty();
  return d;
}
function queuePronSave(){
  if (pronBroken) return;
  clearTimeout(pronTimer); pronStatus('Saving…');
  pronTimer = setTimeout(savePron, 700);
}
async function savePron(){
  const r = await api('/api/pronunciations', {words: pronRows()});
  if (!r.ok) { pronStatus(await r.text(), 'bad'); return; }
  const half = pronRows().filter(([w, s]) => !!w.trim() !== !!s.trim()).length;
  pronStatus(half ? 'Saved. Rows missing either side are skipped until both are filled in.' : 'Saved.', 'ok');
}
async function hear(row){
  const word = row.querySelector('.p-word').value.trim(), say = row.querySelector('.p-say').value.trim();
  if (!word && !say) { pronStatus('Type a word first, then press Hear it.', 'bad'); return; }
  if (!engineReady()) { pronStatus(ENGINE === 'kokoro' ? 'Download the voice model first.' : 'Finish setting up Chatterbox first, or switch to Kokoro.', 'bad'); return; }
  const btn = row.querySelector('.hear'); btn.disabled = true; pronStatus(`Speaking "${say || word}"…`);
  try {
    const r = await api('/api/render', {engine: ENGINE, preset: {...settings(), fixes: false}, text: say || word});
    if (!r.ok) { pronStatus(await r.text(), 'bad'); return; }
    if (ENGINE === 'kokoro') { STATE.kokoro.loaded = true; STATE.chatterbox.running = false; }
    else { STATE.chatterbox.running = true; STATE.kokoro.loaded = false; }
    new Audio(URL.createObjectURL(await r.blob())).play().catch(() => {});
    pronStatus('');
  } finally { btn.disabled = false; }
}
$('pron-add').addEventListener('click', () => pronRow('', '').querySelector('.p-word').focus());
$('pron-from-sel').addEventListener('click', () => {
  const ta = $('text'), sel = ta.value.substring(ta.selectionStart, ta.selectionEnd).trim();
  if (!sel) { pronStatus('Select a word in the script first, then press this button.', 'bad'); return; }
  if (sel.includes('\n')) { pronStatus('Select a word or short phrase on one line.', 'bad'); return; }
  const have = [...$('pron-rows').children].find(r => r.querySelector('.p-word').value === sel);
  (have || pronRow(sel, '')).querySelector('.p-say').focus();
  pronStatus(have ? `"${sel}" is already in the list. Change how it should sound here.` : `Now type how "${sel}" should sound.`);
});
async function loadPron(){
  const p = await (await fetch('/api/pronunciations')).json();
  pronBroken = !!p.problem;
  $('pron-problem').innerHTML = p.problem ? `<div class="note">${esc(p.problem)} Editing is paused until it's fixed, so nothing in the file gets lost. Reload this page after fixing it.</div>` : '';
  $('pron-rows').innerHTML = ''; p.words.forEach(([w, s]) => pronRow(w, s)); pronEmpty();
  $('pron-add').disabled = $('pron-from-sel').disabled = pronBroken;
  if (p.lines) pronStatus(`Plus ${p.lines} whole-line replacement${p.lines === 1 ? '' : 's'} set directly in overrides.json.`);
}

/* ── start-up ──────────────────────────────────────────────────────────────── */
const K = PRESETS.kokoro, C = PRESETS.chatterbox;
K.voices.forEach(([v, w]) => voiceRow(v, w)); drawBlend();
slider('k_speed', K.speed, v => v.toFixed(2) + '×, ' + band(v, 0.93, 1.07, 'slower', 'normal pace', 'faster'));
slider('k_gap_line', K.gap_line, secs); slider('k_gap_head', K.gap_head, secs);
$('k_fixes').checked = K.fixes;
$('voices-dir').textContent = STATE.chatterbox.voices_dir;
$('c_ref').innerHTML = SAMPLES.map(r => `<option value="${esc(r)}"${r === C.ref ? ' selected' : ''}>${esc(r === BUILTIN ? "Chatterbox's own voice" : 'Copy: ' + r.replace(/\.wav$/i, ''))}</option>`).join('');
slider('c_exaggeration', C.exaggeration, v => v.toFixed(2) + ', ' + band(v, 0.35, 0.75, 'calm', 'natural', 'dramatic'));
slider('c_cfg_weight', C.cfg_weight, v => v.toFixed(2) + ', ' + band(v, 0.35, 0.65, 'slower', 'normal', 'brisk'));
slider('c_temperature', C.temperature, v => v.toFixed(2) + ', ' + band(v, 0.65, 0.95, 'steady', 'balanced', 'lively'));
slider('c_gap_line', C.gap_line, secs); slider('c_gap_head', C.gap_head, secs);
KNOBS.forEach(k => $('c_' + k).addEventListener('input', markStyle)); markStyle();
$('c_seed').value = C.seed; $('c_fixes').checked = C.fixes;
$('text').value = SAMPLE; drawMirror();
ping(); showDownload(); loadPron();
if (STATE.kokoro.download.running) pollDownload();
showEngine(store.get('engine') === 'chatterbox' ? 'chatterbox' : 'kokoro');
drawWave();
if (document.fonts) document.fonts.ready.then(() => { drawMirror(); drawWave(); });  // line wrapping changes once the fonts arrive

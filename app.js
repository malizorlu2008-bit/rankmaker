const canvas = document.getElementById('stage');
const ctx = canvas.getContext('2d');
const video = document.getElementById('sourceVideo');
const itemsList = document.getElementById('itemsList');
const itemRowTemplate = document.getElementById('itemRowTemplate');
const statusText = document.getElementById('statusText');
const progressFill = document.getElementById('progressFill');
const captionPreview = document.getElementById('captionPreview');

const RANK_COLORS = ['#ffd400', '#e8e8f0', '#ff9a3c'];
const RANK_COLOR_DEFAULT = '#ffffff';
const TITLE_BAR_RATIO = 0.20;

const ADJECTIVES = ['Best', 'Funniest', 'Craziest', 'Most Satisfying', 'Worst', 'Most Painful', 'Luckiest', 'Weirdest', 'Most Awkward', 'Most Savage', 'Wildest', 'Most Embarrassing', 'Most Epic', 'Unluckiest'];
const TOPICS = ['Waterpark', 'Trampoline Park', 'Gym Fail', 'Wedding', 'Birthday Cake', 'Skiing', 'Parachute', 'Sticker', 'Parkour', 'First Date', 'Job Interview', 'Haircut', 'DIY', 'Pool Party', 'Road Trip', 'School', 'Camping', 'Fishing', 'Cooking', 'Dog Training', 'Bowling', 'Golf'];

let items = [];
let itemIdCounter = 0;
let currentIndex = -1;
let isPlaying = false;
let audioCtx = null;
let sourceNode = null;
let destNode = null;
let rafHandle = null;
let lastExportUrl = null;

function createItem() {
  itemIdCounter += 1;
  return { id: itemIdCounter, file: null, url: null, label: '', emoji: '', start: 0, trim: null, duration: null };
}

function addItem() {
  const item = createItem();
  items.push(item);
  renderItemsList();
}

function removeItem(id) {
  items = items.filter(i => i.id !== id);
  renderItemsList();
}

function moveItem(id, direction) {
  const idx = items.findIndex(i => i.id === id);
  const swapWith = idx + direction;
  if (swapWith < 0 || swapWith >= items.length) return;
  [items[idx], items[swapWith]] = [items[swapWith], items[idx]];
  renderItemsList();
}

const TIKWM_API = 'https://www.tikwm.com/api/';

function isLikelyTiktokLink(link) {
  return /tiktok\.com|douyin\.com/i.test(link);
}

async function fetchTiktokDownloadInfo(link) {
  const apiUrl = `${TIKWM_API}?url=${encodeURIComponent(link)}`;
  const res = await fetch(apiUrl);
  if (!res.ok) throw new Error('TikTok servisi yanıt vermedi');
  const json = await res.json();
  if (json.code !== 0 || !json.data) throw new Error(json.msg || 'Video bulunamadı');
  const videoUrl = json.data.play || json.data.hdplay;
  if (!videoUrl) throw new Error('İndirme linki alınamadı');
  return { videoUrl, title: json.data.title || 'TikTok video', duration: json.data.duration || null };
}

async function handleTiktokFetch(item, linkInput, linkBtn, linkStatus, fileName, startInput, durationInput) {
  const link = linkInput.value.trim();
  if (!link) {
    linkStatus.textContent = 'Önce bir TikTok linki yapıştır.';
    linkStatus.className = 'item-link-status error';
    return;
  }
  if (!isLikelyTiktokLink(link)) {
    linkStatus.textContent = 'Bu bir TikTok linkine benzemiyor.';
    linkStatus.className = 'item-link-status error';
    return;
  }

  linkBtn.disabled = true;
  linkStatus.textContent = 'Video bilgisi alınıyor…';
  linkStatus.className = 'item-link-status';

  try {
    const { videoUrl, title, duration } = await fetchTiktokDownloadInfo(link);
    linkStatus.textContent = 'Filigransız video indiriliyor…';
    const videoRes = await fetch(videoUrl);
    if (!videoRes.ok) throw new Error('Video indirilemedi');
    const blob = await videoRes.blob();

    if (item.url) URL.revokeObjectURL(item.url);
    item.file = null;
    item.url = URL.createObjectURL(blob);
    item.duration = duration;
    item.sourceName = duration ? `${title} (${Math.round(duration)}sn)` : title;
    fileName.textContent = item.sourceName;

    linkStatus.textContent = 'Filigransız video eklendi ✔';
    linkStatus.className = 'item-link-status success';
    drawFrame();

    // Klip maks. süreden uzunsa asıl aksiyon anını otomatik bul.
    const maxClip = getMaxClipSeconds();
    if (duration && duration > maxClip) {
      await autoDetectStart(item, linkStatus, startInput, durationInput);
      drawFrame();
    }
  } catch (err) {
    linkStatus.textContent = err.message || 'Video alınamadı, linki kontrol et.';
    linkStatus.className = 'item-link-status error';
  } finally {
    linkBtn.disabled = false;
  }
}

function renderItemsList() {
  itemsList.innerHTML = '';
  items.forEach((item, index) => {
    const node = itemRowTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector('.item-rank').textContent = index + 1;

    const fileInput = node.querySelector('.item-file');
    const fileName = node.querySelector('.item-file-name');
    fileName.textContent = item.file ? item.file.name : (item.sourceName || 'Klip seçilmedi');
    fileInput.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      if (item.url) URL.revokeObjectURL(item.url);
      item.file = file;
      item.url = URL.createObjectURL(file);
      item.sourceName = null;
      item.duration = null;
      fileName.textContent = file.name;
      const probe = document.createElement('video');
      probe.preload = 'metadata';
      probe.onloadedmetadata = () => {
        if (isFinite(probe.duration)) {
          item.duration = probe.duration;
          fileName.textContent = `${file.name} (${Math.round(probe.duration)}sn)`;
        }
      };
      probe.src = item.url;
      drawFrame();
    });

    const linkInput = node.querySelector('.item-tiktok-link');
    const linkStatus = node.querySelector('.item-link-status');
    const linkBtn = node.querySelector('.link-fetch-btn');

    const runFetch = () => handleTiktokFetch(item, linkInput, linkBtn, linkStatus, fileName, startInput, durationInput);
    linkBtn.addEventListener('click', runFetch);
    linkInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); runFetch(); }
    });

    const labelInput = node.querySelector('.item-label');
    labelInput.value = item.label;
    labelInput.addEventListener('input', (e) => { item.label = e.target.value; drawFrame(); });

    const emojiInput = node.querySelector('.item-emoji');
    emojiInput.value = item.emoji;
    emojiInput.addEventListener('input', (e) => { item.emoji = e.target.value; drawFrame(); });

    node.querySelectorAll('.emoji-quick button').forEach(btn => {
      btn.addEventListener('click', () => {
        item.emoji = btn.dataset.emoji;
        emojiInput.value = item.emoji;
        drawFrame();
      });
    });

    const startInput = node.querySelector('.item-start');
    startInput.value = item.start || '';
    startInput.addEventListener('input', (e) => {
      item.start = parseFloat(e.target.value) || 0;
    });

    const durationInput = node.querySelector('.item-duration');
    durationInput.value = item.trim || '';
    durationInput.addEventListener('input', (e) => {
      item.trim = parseFloat(e.target.value) || null;
    });

    const detectBtn = node.querySelector('.detect-btn');
    detectBtn.addEventListener('click', async () => {
      detectBtn.disabled = true;
      await autoDetectStart(item, linkStatus, startInput, durationInput);
      detectBtn.disabled = false;
      drawFrame();
    });

    node.querySelector('.move-up').addEventListener('click', () => moveItem(item.id, -1));
    node.querySelector('.move-down').addEventListener('click', () => moveItem(item.id, 1));
    node.querySelector('.remove').addEventListener('click', () => removeItem(item.id));

    itemsList.appendChild(node);
  });
}

const MOTION_SAMPLE_STEP = 0.3;

// Klibi tarayıp kare-kare hareket miktarını ölçer.
async function analyzeMotion(url, onProgress) {
  const v = document.createElement('video');
  v.preload = 'auto';
  v.muted = true;
  v.playsInline = true;
  v.src = url;
  await new Promise((res, rej) => {
    v.onloadeddata = res;
    v.onerror = () => rej(new Error('Video okunamadı'));
  });

  const dur = v.duration;
  if (!isFinite(dur) || dur <= 0) throw new Error('Klip süresi okunamadı');

  const w = 64;
  const h = 114;
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const cx = c.getContext('2d', { willReadFrequently: true });

  // 4x4x4 RGB histogramı — sahne kesmesini hareketten ayırmak için.
  const histOf = (data) => {
    const bins = new Float32Array(64);
    const px = data.length / 4;
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i] >> 6;
      const g = data[i + 1] >> 6;
      const b = data[i + 2] >> 6;
      bins[r * 16 + g * 4 + b] += 1;
    }
    for (let i = 0; i < 64; i += 1) bins[i] /= px;
    return bins;
  };

  const samples = [];
  let prev = null;
  let prevHist = null;

  for (let t = 0; t < dur; t += MOTION_SAMPLE_STEP) {
    v.currentTime = t;
    await new Promise((res) => { v.onseeked = res; });
    cx.drawImage(v, 0, 0, w, h);
    const data = cx.getImageData(0, 0, w, h).data;
    const hist = histOf(data);

    let motion = 0;
    let histDiff = 0;
    if (prev) {
      for (let i = 0; i < data.length; i += 4) {
        motion += Math.abs(data[i] - prev[i])
          + Math.abs(data[i + 1] - prev[i + 1])
          + Math.abs(data[i + 2] - prev[i + 2]);
      }
      motion /= (data.length / 4);
      for (let i = 0; i < 64; i += 1) histDiff += Math.abs(hist[i] - prevHist[i]);
    }
    samples.push({ t, motion, histDiff });
    prev = data.slice(0);
    prevHist = hist;
    if (onProgress) onProgress(t / dur);
  }

  return { duration: dur, samples };
}

// Sahne kesmesi = renk dağılımının aniden değişmesi.
// (Suya çarpma gibi hareketler renk dağılımını korur, bu yüzden kesme sayılmaz.)
const CUT_HIST_THRESHOLD = 0.45;

function findSceneCuts(samples) {
  const cuts = [];
  for (let i = 1; i < samples.length - 1; i += 1) {
    const d = samples[i].histDiff || 0;
    if (d < CUT_HIST_THRESHOLD) continue;
    // yerel tepe olmalı ki tek bir geçiş birden çok kesme sayılmasın
    if (d >= (samples[i - 1].histDiff || 0) && d >= (samples[i + 1].histDiff || 0)) {
      if (!cuts.length || samples[i].t - cuts[cuts.length - 1] > 1.0) cuts.push(samples[i].t);
    }
  }
  return cuts;
}

// Kesmelerle bölünmüş segmentler içinde en hareketli pencereyi bulur;
// böylece seçilen aralık iki farklı klibin üzerinden atlamaz.
function bestMotionWindow(samples, duration, windowSec) {
  const L = Math.min(windowSec, duration);

  // Klip pencereden çok uzun değilse SONU al. Fail videolarında asıl an (düşme,
  // çarpma, tepki) neredeyse her zaman klibin sonundadır; baştan kırpmak
  // izleyiciyi asıl anı göremeden bırakır.
  if (duration <= L * 1.7) {
    return { start: Math.max(0, duration - L), length: L };
  }

  const cuts = findSceneCuts(samples);

  // Kesme noktalarından segment sınırları oluştur.
  const bounds = [0, ...cuts, duration];
  const rawSegments = [];
  for (let i = 0; i < bounds.length - 1; i += 1) {
    rawSegments.push({ from: bounds[i], to: bounds[i + 1] });
  }

  // Çok kısa segmentleri ele: aksi halde POV/hızlı kamera hareketi olan tek çekim
  // klipler yanlışlıkla parçalanıp 2-3 saniyelik kırpıntıya düşer.
  const minSegment = Math.min(Math.max(4, L * 0.5), duration);
  let segments = rawSegments.filter(s => s.to - s.from >= minSegment);
  // Hiçbir segment yeterince uzun değilse kesmeleri yok say (muhtemelen yanlış tespit).
  if (!segments.length) segments = [{ from: 0, to: duration }];

  // Fail videolarında asıl an (düşme/çarpma) kısa ve keskin bir hareket tepesidir
  // ve çoğu zaman klibin SONUNA yakındır. Bu yüzden "en çok toplam hareket" yerine
  // tepe anını bulup pencereyi onun etrafına kuruyoruz: öncesinde hazırlık,
  // sonrasında tepki payı. Böylece asıl an asla kesilmez.
  const cutTimes = new Set(cuts.map(c => c.toFixed(2)));

  let chosen = null;
  for (const seg of segments) {
    const segLen = seg.to - seg.from;
    let peak = { t: seg.from, m: -1 };
    for (const s of samples) {
      // segment sınırlarındaki geçiş karelerini tepe saymayalım
      if (s.t < seg.from + 0.35 || s.t > seg.to - 0.05) continue;
      if (cutTimes.has(s.t.toFixed(2))) continue;
      if (s.motion > peak.m) peak = { t: s.t, m: s.motion };
    }
    if (peak.m < 0) continue;
    // Tepe kadar güçlü ama DAHA GEÇ gelen bir an varsa onu tercih et:
    // giriş sarsıntısı çoğu zaman en yüksek harekettir, asıl an ise sonda gelir.
    for (const s of samples) {
      if (s.t <= peak.t) continue;
      if (s.t < seg.from + 0.35 || s.t > seg.to - 0.05) continue;
      if (cutTimes.has(s.t.toFixed(2))) continue;
      if (s.motion >= peak.m * 0.72) peak = { t: s.t, m: s.motion };
    }
    // Kompilasyonlarda en güçlü aksiyona sahip segmenti seç
    const score = peak.m * Math.min(1, segLen / L);
    if (!chosen || score > chosen.score) chosen = { seg, peak, score, winLen: Math.min(L, segLen) };
  }

  if (!chosen) return { start: 0, length: L };

  const { seg, peak, winLen } = chosen;
  // Tepe anından sonra bırakılacak pay (tepki/sonuç görünsün)
  const tail = Math.min(winLen * 0.3, 2.5);
  let start = peak.t - (winLen - tail);
  start = Math.min(start, seg.to - winLen);
  start = Math.max(start, seg.from, 0);

  return { start, length: winLen };
}

async function autoDetectStart(item, statusEl, startInput, durationInputRef) {
  if (!item.url) {
    if (statusEl) {
      statusEl.textContent = 'Önce klip yükle.';
      statusEl.className = 'item-link-status error';
    }
    return null;
  }
  if (statusEl) {
    statusEl.textContent = 'Aksiyon anı aranıyor…';
    statusEl.className = 'item-link-status';
  }
  try {
    const { duration, samples } = await analyzeMotion(item.url);
    item.duration = duration;
    const windowSec = item.trim || Math.min(getMaxClipSeconds(), duration);
    const { start, length } = bestMotionWindow(samples, duration, windowSec);
    item.start = Math.round(start * 10) / 10;
    // Segment penceresi tam uzunluktan kısaysa (kompilasyon klibi) süreyi ona sabitle.
    if (length && length < windowSec - 0.4) {
      item.trim = Math.round(length * 10) / 10;
      if (durationInputRef) durationInputRef.value = item.trim;
    }
    if (startInput) startInput.value = item.start || '';
    if (statusEl) {
      const shown = item.trim ? ` (${item.trim}sn)` : '';
      statusEl.textContent = `Aksiyon anı: ${item.start}sn${shown} ✔`;
      statusEl.className = 'item-link-status success';
    }
    return item.start;
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err.message || 'Aksiyon anı bulunamadı.';
      statusEl.className = 'item-link-status error';
    }
    return null;
  }
}

function getTitleConfig() {
  return {
    prefix: document.getElementById('prefixText').value || '',
    prefixColor: document.getElementById('prefixColor').value,
    adjective: document.getElementById('adjectiveText').value || '',
    adjectiveColor: document.getElementById('adjectiveColor').value,
    topic: document.getElementById('topicText').value || '',
    topicColor: document.getElementById('topicColor').value,
    suffix: document.getElementById('suffixText').value || '',
    suffixColor: document.getElementById('suffixColor').value,
  };
}

function fitFontSize(text, maxWidth, startSize, fontWeight) {
  let size = startSize;
  ctx.font = `${fontWeight} ${size}px Poppins, sans-serif`;
  while (ctx.measureText(text).width > maxWidth && size > 24) {
    size -= 2;
    ctx.font = `${fontWeight} ${size}px Poppins, sans-serif`;
  }
  return size;
}

function drawStrokedSegments(segments, centerX, y, fontSize, fontWeight) {
  const fullText = segments.map(s => s.text).join(' ');
  ctx.font = `${fontWeight} ${fontSize}px Poppins, sans-serif`;
  const totalWidth = ctx.measureText(fullText).width;
  let x = centerX - totalWidth / 2;
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  segments.forEach((seg, i) => {
    const word = seg.text + (i < segments.length - 1 ? ' ' : '');
    ctx.lineWidth = fontSize * 0.11;
    ctx.strokeStyle = 'rgba(0,0,0,0.9)';
    ctx.strokeText(word, x, y);
    ctx.fillStyle = seg.color;
    ctx.fillText(word, x, y);
    x += ctx.measureText(word).width;
  });
}

function drawTitle() {
  const cfg = getTitleConfig();
  const barHeight = canvas.height * TITLE_BAR_RATIO;
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, canvas.width, barHeight);

  const maxWidth = canvas.width * 0.9;
  const line1 = [
    { text: cfg.prefix, color: cfg.prefixColor },
    { text: cfg.adjective, color: cfg.adjectiveColor },
  ].filter(s => s.text);
  const line2 = [
    { text: cfg.topic, color: cfg.topicColor },
    { text: cfg.suffix, color: cfg.suffixColor },
  ].filter(s => s.text);

  const line1Text = line1.map(s => s.text).join(' ');
  const line2Text = line2.map(s => s.text).join(' ');
  const size1 = fitFontSize(line1Text, maxWidth, 78, 800);
  const size2 = fitFontSize(line2Text, maxWidth, 78, 800);
  const fontSize = Math.min(size1, size2);

  const centerX = canvas.width / 2;
  const lineGap = fontSize * 1.15;
  const midY = barHeight / 2;
  drawStrokedSegments(line1, centerX, midY - lineGap / 2, fontSize, 800);
  drawStrokedSegments(line2, centerX, midY + lineGap / 2, fontSize, 800);
}

function drawVideoFrame() {
  const barHeight = canvas.height * TITLE_BAR_RATIO;
  const areaY = barHeight;
  const areaH = canvas.height - barHeight;
  const areaW = canvas.width;

  ctx.fillStyle = '#111114';
  ctx.fillRect(0, areaY, areaW, areaH);

  const current = items[currentIndex];
  if (current && current.url && video.readyState >= 2 && video.videoWidth) {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const scale = Math.max(areaW / vw, areaH / vh);
    const dw = vw * scale;
    const dh = vh * scale;
    const dx = (areaW - dw) / 2;
    const dy = areaY + (areaH - dh) / 2;
    ctx.drawImage(video, dx, dy, dw, dh);
  } else {
    ctx.fillStyle = '#5a5a72';
    ctx.font = '600 34px Poppins, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Klip bekleniyor…', areaW / 2, areaY + areaH / 2);
    ctx.textAlign = 'left';
  }
}

function drawList() {
  const barHeight = canvas.height * TITLE_BAR_RATIO;
  const paddingLeft = canvas.width * 0.07;
  const startY = barHeight + 90;
  const lineHeight = Math.min(120, (canvas.height - barHeight - 120) / Math.max(items.length, 1));
  const fontSize = Math.max(38, Math.min(58, lineHeight * 0.55));

  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';

  items.forEach((item, index) => {
    const rank = index + 1;
    const y = startY + index * lineHeight;
    const revealed = index >= currentIndex || !isPlaying;
    const rankColor = RANK_COLORS[index] || RANK_COLOR_DEFAULT;

    ctx.font = `900 ${fontSize}px Poppins, sans-serif`;
    const numberText = `${rank}.`;
    ctx.lineWidth = fontSize * 0.14;
    ctx.strokeStyle = 'rgba(0,0,0,0.9)';
    ctx.strokeText(numberText, paddingLeft, y);
    ctx.fillStyle = rankColor;
    ctx.fillText(numberText, paddingLeft, y);

    if (revealed && (item.label || item.emoji)) {
      const numberWidth = ctx.measureText(numberText).width;
      let x = paddingLeft + numberWidth + fontSize * 0.35;

      if (item.label) {
        const labelFontSize = fontSize * 0.72;
        ctx.font = `700 ${labelFontSize}px Poppins, sans-serif`;
        ctx.lineWidth = labelFontSize * 0.16;
        ctx.strokeStyle = 'rgba(0,0,0,0.9)';
        ctx.strokeText(item.label, x, y);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(item.label, x, y);
        x += ctx.measureText(item.label).width + fontSize * 0.25;
      }

      if (item.emoji) {
        const emojiFontSize = fontSize * 0.85;
        ctx.font = `${emojiFontSize}px 'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif`;
        ctx.fillText(item.emoji, x, y);
      }
    }
  });
}

function drawFrame() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawVideoFrame();
  drawList();
  drawTitle();
}

function loop() {
  drawFrame();
  rafHandle = requestAnimationFrame(loop);
}

function ensureAudioGraph() {
  if (audioCtx) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  sourceNode = audioCtx.createMediaElementSource(video);
  destNode = audioCtx.createMediaStreamDestination();
  sourceNode.connect(destNode);
  sourceNode.connect(audioCtx.destination);
}

function playClip(item, trimSeconds) {
  return new Promise((resolve) => {
    let settled = false;
    const cleanup = () => {
      video.removeEventListener('ended', onEnded);
      video.removeEventListener('loadedmetadata', onMeta);
      clearTimeout(timer);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      cleanup();
      video.pause();
      resolve();
    };
    const onEnded = () => finish();
    let timer = null;
    const onMeta = () => {
      const duration = video.duration || 0;
      const startAt = Math.min(Math.max(item.start || 0, 0), duration);
      video.currentTime = startAt;
      const remaining = duration - startAt;
      const limit = trimSeconds ? Math.min(trimSeconds, remaining || trimSeconds) : remaining;
      if (limit && isFinite(limit) && limit > 0) {
        timer = setTimeout(finish, limit * 1000);
      }
    };
    video.addEventListener('ended', onEnded, { once: true });
    video.addEventListener('loadedmetadata', onMeta, { once: true });
    video.src = item.url;
    video.muted = false;
    video.play().catch(() => {});
  });
}

function getMaxClipSeconds() {
  return parseFloat(document.getElementById('defaultTrim').value) || 15;
}

async function playSequence() {
  const maxClip = getMaxClipSeconds();
  currentIndex = items.length;
  const playOrder = [];
  for (let i = items.length - 1; i >= 0; i -= 1) playOrder.push(i);
  for (let step = 0; step < playOrder.length; step += 1) {
    const i = playOrder[step];
    if (!isPlaying) break;
    if (!items[i].url) continue;
    currentIndex = i;
    setStatus(`Sıra ${i + 1} (${step + 1}/${items.length}) oynatılıyor…`);
    setProgress((step / items.length) * 100);
    // item.trim varsa o klibe özel süre; yoksa tam oyna ama maxClip'i aşma
    await playClip(items[i], items[i].trim || maxClip);
  }
}

function setStatus(text) { statusText.textContent = text; }
function setProgress(pct) { progressFill.style.width = `${Math.max(0, Math.min(100, pct))}%`; }

async function handlePlayPreview() {
  if (!items.some(i => i.url)) {
    setStatus('Önce en az bir klip yükle.');
    return;
  }
  ensureAudioGraph();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  isPlaying = true;
  await playSequence();
  isPlaying = false;
  setStatus('Önizleme bitti');
  setProgress(0);
}

async function handleExport() {
  if (!items.some(i => i.url)) {
    setStatus('Önce en az bir klip yükle.');
    return;
  }
  const exportBtn = document.getElementById('exportBtn');
  const playBtn = document.getElementById('playBtn');
  exportBtn.disabled = true;
  playBtn.disabled = true;

  ensureAudioGraph();
  if (audioCtx.state === 'suspended') await audioCtx.resume();

  const fps = parseInt(document.getElementById('fpsInput').value, 10) || 30;
  const canvasStream = canvas.captureStream(fps);
  const combined = new MediaStream([
    ...canvasStream.getVideoTracks(),
    ...destNode.stream.getAudioTracks(),
  ]);

  let mimeType = 'video/webm;codecs=vp9,opus';
  if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'video/webm';

  const maxClip = getMaxClipSeconds();
  const clipSeconds = (i) => {
    if (i.trim) return i.trim;
    const dur = i.duration ? Math.max(0, i.duration - (i.start || 0)) : maxClip;
    return Math.min(dur || maxClip, maxClip);
  };
  const estimatedSeconds = Math.max(1, items.reduce((sum, i) => sum + (i.url ? clipSeconds(i) : 0), 0));
  const targetFileBytes = 9 * 1024 * 1024;
  const videoBitsPerSecond = Math.min(8_000_000, Math.max(2_000_000, Math.floor((targetFileBytes * 8) / estimatedSeconds)));

  const recorder = new MediaRecorder(combined, { mimeType, videoBitsPerSecond });
  const chunks = [];
  recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };

  const stopped = new Promise((resolve) => { recorder.onstop = resolve; });

  const totalSec = Math.round(estimatedSeconds);
  setStatus(totalSec > 60 ? `Kayıt başladı… (~${totalSec}sn — Shorts için uzun, maks. süreyi düşürebilirsin)` : `Kayıt başladı… (~${totalSec}sn)`);
  recorder.start();
  isPlaying = true;
  await playSequence();
  isPlaying = false;
  recorder.stop();
  await stopped;

  if (lastExportUrl) URL.revokeObjectURL(lastExportUrl);
  const blob = new Blob(chunks, { type: 'video/webm' });
  const url = URL.createObjectURL(blob);
  lastExportUrl = url;
  const titleCfg = getTitleConfig();
  const filename = `${[titleCfg.prefix, titleCfg.adjective, titleCfg.topic, titleCfg.suffix].join('-')}`
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'ranking-video';

  const a = document.createElement('a');
  a.href = url;
  a.download = `${filename}.webm`;
  document.body.appendChild(a);
  a.click();
  a.remove();

  const manualLink = document.getElementById('manualDownloadLink');
  manualLink.href = url;
  manualLink.download = `${filename}.webm`;
  manualLink.style.display = 'inline-block';

  setStatus('Video indirildi ✔');
  setProgress(0);
  exportBtn.disabled = false;
  playBtn.disabled = false;
}

function applyRandomIdea() {
  const adjective = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const topic = TOPICS[Math.floor(Math.random() * TOPICS.length)];
  document.getElementById('adjectiveText').value = adjective;
  document.getElementById('topicText').value = topic;
  drawFrame();
}

function bindStaticControls() {
  document.querySelectorAll('#prefixText, #adjectiveText, #topicText, #suffixText, #prefixColor, #adjectiveColor, #topicColor, #suffixColor')
    .forEach(el => el.addEventListener('input', drawFrame));

  document.getElementById('captionText').addEventListener('input', (e) => {
    captionPreview.textContent = e.target.value;
  });

  document.getElementById('addItemBtn').addEventListener('click', addItem);
  document.getElementById('randomIdeaBtn').addEventListener('click', applyRandomIdea);
  document.getElementById('playBtn').addEventListener('click', handlePlayPreview);
  document.getElementById('exportBtn').addEventListener('click', handleExport);
}

function init() {
  bindStaticControls();
  addItem();
  addItem();
  addItem();
  drawFrame();
  loop();
}

init();

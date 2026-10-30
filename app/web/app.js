'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const DIMS = [
  ['jd_match', 'JD 关键词匹配', 25],
  ['quantified', '成果量化', 20],
  ['structure', '结构与重点', 15],
  ['star', '表达质量（STAR）', 15],
  ['completeness', '硬性信息完整', 15],
  ['details', '细节规范', 10],
];

const STAGES = ['解析简历', '提取 JD 要求', '知识库检索', '补充确认', '逐维度评分',
  '生成改写示例', '护栏检查', '版本对比', '完成'];

const DIM_LABEL = {
  jd_match: 'JD 关键词匹配', quantified: '成果量化', structure: '结构与重点',
  star: '表达质量（STAR）', completeness: '硬性信息完整', details: '细节规范',
};

const state = {
  userId: localStorage.getItem('rd_user') || '',
  sessionId: localStorage.getItem('rd_session') || ('s_' + Math.random().toString(36).slice(2, 10)),
  file: null, fileB64: null, fileName: '',
  job: null, report: null, health: null, timer: null,
  adopted: {}, hrMap: {}, calib: null,
};
localStorage.setItem('rd_session', state.sessionId);

/* ---------------- utils ---------------- */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function scoreColor(v) {
  if (v >= 85) return '#0f9d58';
  if (v >= 75) return '#2563eb';
  if (v >= 60) return '#d97706';
  return '#d92d20';
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function post(path, body) {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}
function toast(msg) {
  const d = document.createElement('div');
  d.className = 'toast';
  d.textContent = msg;
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 2600);
}
function fmtDelta(v) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + v;
}

/* ---------------- tabs ---------------- */
function setTab(name) {
  $$('.tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === name));
  $$('.view').forEach((x) => x.classList.toggle('active', x.id === 'view-' + name));
  if (name === 'history') loadHistory();
  if (name === 'calib') loadCalib();
  if (name === 'adopt') loadAdopt();
}
$$('.tab').forEach((t) => t.addEventListener('click', () => setTab(t.dataset.tab)));

/* ---------------- health ---------------- */
async function health() {
  try {
    const h = await api('/api/health');
    state.health = h;
    const el = $('#pill-engine');
    el.textContent = h.has_key ? ('引擎 ' + h.model + ' · 在线') : '引擎 · 无 Key（将走离线）';
    el.className = 'pill ' + (h.has_key ? 'ok' : 'warn');
    $('#pill-kb').textContent = '知识库 ' + h.kb_docs + ' 段 / 依据 ' +
      (h.rubric || []).reduce((a, x) => a + (x.bands || []).length, 0) + ' 档';
    $('#pill-kb').className = 'pill ok';
  } catch (e) {
    $('#pill-engine').textContent = '服务未连接 · 请先双击「启动.bat」';
    $('#pill-engine').className = 'pill bad';
  }
  $('#pill-user').textContent = '用户 ' + (state.userId || '（首次诊断后生成）');
}

/* ---------------- file ---------------- */
const drop = $('#drop');
const fileInput = $('#file');
drop.addEventListener('click', () => fileInput.click());
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) handleFile(f);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});
function handleFile(f) {
  if (!/\.(pdf|docx|txt|md)$/i.test(f.name)) { toast('只支持 PDF / DOCX / TXT'); return; }
  if (f.size > 8 * 1024 * 1024) { toast('文件过大（>8MB）'); return; }
  state.file = f; state.fileName = f.name;
  const reader = new FileReader();
  reader.onload = () => {
    state.fileB64 = String(reader.result).split(',')[1];
    const info = $('#fileinfo');
    info.classList.remove('hidden');
    info.textContent = '已选择：' + f.name + '（' + (f.size / 1024).toFixed(0) + ' KB）';
  };
  reader.readAsDataURL(f);
}

/* ---------------- 示例数据 ---------------- */
$$('[data-sample]').forEach((btn) => btn.addEventListener('click', async () => {
  try {
    const d = await api('/api/sample?name=' + btn.dataset.sample);
    $('#resume-text').value = d.text;
    state.fileB64 = null; state.file = null; state.fileName = d.filename || 'sample.txt';
    $('#fileinfo').classList.add('hidden');
    const jd = await api('/api/sample?name=jd');
    if (!$('#jd-text').value.trim()) $('#jd-text').value = jd.text;
    if (btn.dataset.sample !== 'jd' && !$('#label').value.trim()) {
      $('#label').value = d.title || '示例诊断';
    }
    toast('已填入：' + (d.title || '示例'));
  } catch (e) { toast('读取示例失败：' + e.message); }
}));

/* ---------------- 诊断 ---------------- */
$('#go').addEventListener('click', startAnalyze);

async function startAnalyze() {
  const jd = $('#jd-text').value.trim();
  const text = $('#resume-text').value.trim();
  if (!jd) { toast('请先粘贴目标岗位 JD'); return; }
  if (!state.fileB64 && text.length < 80) { toast('请上传简历文件，或粘贴 ≥80 字的简历正文'); return; }

  const btn = $('#go');
  btn.disabled = true; btn.textContent = '诊断中…';
  $('#clarify-card').classList.add('hidden');
  $('#progress-card').classList.remove('hidden');
  $('#p-steps').innerHTML = stepsHTML(null);
  $('#p-logs').innerHTML = '';
  setProgress('准备中', 2, '正在上传并解析…');

  try {
    const r = await post('/api/analyze', {
      user_id: state.userId || null,
      session_id: state.sessionId,
      filename: state.fileName || '粘贴文本',
      jd_text: jd,
      resume_text: state.fileB64 ? null : text,
      file_b64: state.fileB64,
      label: $('#label').value.trim() || null,
      engine: $('#engine').value,
    });
    state.job = r.job_id;
    pollJob(r.job_id);
  } catch (e) {
    btn.disabled = false; btn.textContent = '开始诊断';
    setProgress('出错', 0, e.message);
    toast('启动失败：' + e.message);
  }
}

function stepsHTML(cur) {
  const idx = STAGES.indexOf(cur);
  return STAGES.map((s, i) => {
    const cls = idx === -1 ? '' : (i < idx ? 'done' : (i === idx ? 'cur' : ''));
    return '<li class="' + cls + '">' + s + '</li>';
  }).join('');
}
function setProgress(stage, pct, msg) {
  $('#p-stage').textContent = stage + (msg ? ' · ' + msg : '');
  $('#p-pct').textContent = (pct || 0) + '%';
  $('#p-bar').style.width = Math.max(2, pct || 0) + '%';
  $('#p-steps').innerHTML = stepsHTML(stage);
}

function pollJob(jid) {
  if (state.timer) clearTimeout(state.timer);
  const tick = async () => {
    try {
      const j = await api('/api/job/' + jid);
      const p = j.progress || {};
      setProgress(p.stage || '处理中', p.pct || 0, p.msg || '');
      if (j.logs && j.logs.length) {
        $('#p-logs').innerHTML = j.logs.map((l) =>
          '<div>[' + esc(l.t) + '] ' + esc(l.stage) + ' — ' + esc(l.msg) + '</div>').join('');
      }
      if (j.status === 'waiting') {
        renderClarify(j.clarify ? j.clarify.questions : []);
        return;
      }
      if (j.status === 'done') { finishJob(j); return; }
      if (j.status === 'error') {
        setProgress('失败', 0, j.error || '未知错误');
        const btn = $('#go'); btn.disabled = false; btn.textContent = '开始诊断';
        toast('诊断失败：' + (j.error || ''));
        return;
      }
      state.timer = setTimeout(tick, 900);
    } catch (e) {
      state.timer = setTimeout(tick, 1500);
    }
  };
  tick();
}

function finishJob(j) {
  state.report = j.report;
  state.userId = j.report.user_id;
  localStorage.setItem('rd_user', state.userId);
  $('#pill-user').textContent = '用户 ' + state.userId;
  renderReport(j.report, $('#report-body'));
  $('#report-empty').classList.add('hidden');
  $('#report-body').classList.remove('hidden');
  setTab('report');
  const btn = $('#go'); btn.disabled = false; btn.textContent = '开始诊断';
  toast('诊断完成：' + j.report.total + ' 分（v' + j.report.version.seq + '）');
}

/* ---------------- 追问 ---------------- */
function renderClarify(questions) {
  const card = $('#clarify-card');
  if (!questions || !questions.length) { card.classList.add('hidden'); return; }
  $('#clarify-count').textContent = questions.length + ' 个问题';
  $('#clarify-list').innerHTML = questions.map((q, i) => (
    '<div class="q-item" data-qid="' + esc(q.id) + '">' +
      '<div class="q-head"><div class="q-idx">' + (i + 1) + '</div>' +
        '<div><div class="q-text">' + esc(q.q) + '</div>' +
        '<div class="q-meta"><span class="tag tag-warn">' +
          esc(DIM_LABEL[q.dim] || q.dim) + '</span>' +
          '<span class="tag">' + esc(q.gain) + '</span>' + esc(q.why) + '</div></div></div>' +
      '<div class="q-chips">' + (q.chips || []).map((c) =>
        '<button class="chip" data-chip="' + esc(q.id) + '" data-val="' + esc(c) + '">' + esc(c) + '</button>').join('') +
      '</div>' +
      '<div class="q-answer"><input type="text" data-ans="' + esc(q.id) + '" placeholder="也可以自己写一句，例如：3 台云主机、约 200 并发" /></div>' +
    '</div>'
  )).join('');
  card.classList.remove('hidden');

  $$('[data-chip]').forEach((b) => b.addEventListener('click', () => {
    const qid = b.dataset.chip;
    const input = $('[data-ans="' + qid + '"]');
    if (input) input.value = b.dataset.val;
    $$('[data-chip="' + qid + '"]').forEach((x) => x.classList.toggle('on', x === b));
  }));
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function collectAnswers() {
  const out = {};
  $$('[data-ans]').forEach((i) => {
    const v = i.value.trim();
    if (v) out[i.dataset.ans] = v;
  });
  return out;
}

$('#submit-clarify').addEventListener('click', () => sendClarify(collectAnswers()));
$('#skip-clarify').addEventListener('click', () => sendClarify({}));

async function sendClarify(answers) {
  if (!state.job) return;
  const n = Object.keys(answers).length;
  try {
    await post('/api/clarify', { job_id: state.job, answers: answers });
    $('#clarify-card').classList.add('hidden');
    setProgress('逐维度评分', 44, n ? ('已补充 ' + n + ' 条信息，开始评分') : '已跳过补充，直接评分');
    pollJob(state.job);
  } catch (e) { toast('提交失败：' + e.message); }
}

/* ---------------- 雷达图 ---------------- */
function drawRadar(canvas, dims) {
  const dpr = window.devicePixelRatio || 1;
  const size = 340;
  canvas.width = size * dpr; canvas.height = size * dpr;
  canvas.style.width = size + 'px'; canvas.style.height = size + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const cx = size / 2, cy = size / 2 + 6, R = size / 2 - 64;
  const n = dims.length;
  const ang = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;

  for (let r = 1; r <= 4; r++) {
    ctx.beginPath();
    for (let i = 0; i <= n; i++) {
      const a = ang(i % n), rr = (R * r) / 4;
      const x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = 'rgba(120,145,180,.22)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  dims.forEach((d, i) => {
    const a = ang(i);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
    ctx.strokeStyle = 'rgba(120,145,180,.2)';
    ctx.stroke();

    const rate = d.score / d.max;
    const lx = cx + Math.cos(a) * (R + 34), ly = cy + Math.sin(a) * (R + 28);
    ctx.font = '12px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = '#5b6b80';
    ctx.textAlign = Math.abs(Math.cos(a)) < 0.3 ? 'center' : (Math.cos(a) > 0 ? 'left' : 'right');
    ctx.textBaseline = 'middle';
    ctx.fillText(d.name.replace('（STAR）', ''), lx, ly - 7);
    ctx.fillStyle = scoreColor(rate * 100);
    ctx.font = 'bold 12px "Microsoft YaHei", sans-serif';
    ctx.fillText(d.score + '/' + d.max, lx, ly + 9);
  });

  ctx.beginPath();
  dims.forEach((d, i) => {
    const a = ang(i), rr = R * (d.score / d.max);
    const x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.closePath();
  const g = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
  g.addColorStop(0, 'rgba(37,99,235,.30)');
  g.addColorStop(1, 'rgba(15,157,88,.24)');
  ctx.fillStyle = g; ctx.fill();
  ctx.strokeStyle = 'rgba(37,99,235,.85)'; ctx.lineWidth = 2; ctx.stroke();

  dims.forEach((d, i) => {
    const a = ang(i), rr = R * (d.score / d.max);
    ctx.beginPath();
    ctx.arc(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 3.4, 0, 7);
    ctx.fillStyle = '#fff'; ctx.fill();
    ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 2; ctx.stroke();
  });
}

/* ---------------- 报告渲染 ---------------- */
function ringSVG(total) {
  const r = 84, c = 2 * Math.PI * r, off = c * (1 - total / 100);
  const col = scoreColor(total);
  return '<svg class="ring-wrap" viewBox="0 0 200 200" width="200" height="200">' +
    '<circle cx="100" cy="100" r="' + r + '" fill="none" stroke="#edf1f6" stroke-width="15"/>' +
    '<circle cx="100" cy="100" r="' + r + '" fill="none" stroke="' + col +
    '" stroke-width="15" stroke-linecap="round" stroke-dasharray="' + c + '" stroke-dashoffset="' + off +
    '" transform="rotate(-90 100 100)"/>' +
    '</svg>' +
    '<div class="ring-center"><div><div class="ring-num" style="color:' + col + '">' +
    total + '</div><div class="ring-sub">总分 / 100</div></div></div>';
}

function refHTML(ref) {
  if (!ref) return '';
  const id = 'rf' + Math.random().toString(36).slice(2, 8);
  return '<div class="ref-badge" data-ref="' + id + '">依据：' + esc(ref.clause) +
    ' <span style="opacity:.6">▾</span></div>' +
    '<div class="ref-body hidden" id="' + id + '">' +
      '<div class="rq">「' + esc(ref.quote) + '」</div>' +
      '<div class="rp">— ' + esc(ref.doc) + ' 第 ' + ref.line + ' 行 · ' +
      esc(ref.dim_name) + '（满分 ' + ref.max + '）</div>' +
      '<div style="margin-top:6px">检查要点原文：' + esc(ref.lead || '') + '</div>' +
    '</div>';
}

function renderReport(r, host) {
  const v = r.version || {};
  const cmp = r.compare;
  const offline = (r.engine || 'llm') !== 'llm';

  const fitCls = { '高度匹配': 'tag-ok', '基本匹配': 'tag-info', '偏低': 'tag-bad' }[r.jd_fit.level] || 'tag-info';
  const cov = (r.metrics || {}).jd_coverage;
  const gauge = cov
    ? '<span class="tag tag-info">JD 关键词覆盖率 ' + Math.round(cov.rate * 100) + '%（' +
      cov.hit.length + '/' + cov.total + '）</span>'
    : '';

  let engineBar = '';
  if (offline) {
    engineBar = '<div class="guard-banner" style="background:#fff7e6;border-color:#f3ddb7">' +
      '⚡ 当前为<b>离线演示模式</b>（规则引擎打分）。原因：' + esc(r.offline_reason || '未配置 API Key') +
      '。改写稿为句式框架，需自行补真实数据。</div>';
  }

  // 追问回放
  const cl = r.clarify || {};
  let clarifyHTML = '';
  if (cl.questions && cl.questions.length) {
    const facts = cl.facts || [];
    const neg = cl.negative || [];
    clarifyHTML = '<div class="card"><h2>补充确认回放 ' +
      '<span class="tag tag-info">问了 ' + cl.questions.length + ' 题 · 答了 ' +
      ((cl.summary || {}).answered || 0) + ' 题</span></h2>' +
      (facts.length || neg.length
        ? '<div class="tagrow">' + facts.map((f) => '<span class="tag tag-ok">✓ ' + esc(f) + '</span>').join('') +
          neg.map((f) => '<span class="tag">✗ ' + esc(f) + '</span>').join('') + '</div>' +
          '<p class="muted small" style="margin-top:8px">这些是你在诊断中补充的信息，已作为可信证据参与打分。</p>'
        : '<p class="muted small">本次全部跳过，未补充额外信息（分数未受补充影响）。</p>') +
      '</div>';
  }

  // 对比
  let cmpHTML = '';
  if (cmp) {
    const dt = cmp.delta_total;
    const cls = dt > 0 ? 'up' : (dt < 0 ? 'down' : '');
    const cells = Object.values(cmp.dims).map((d) => {
      const dv = d.delta;
      const cc = dv > 0 ? 'up' : (dv < 0 ? 'down' : '');
      return '<div class="cmp-cell"><span>' + esc(d.name) + '</span><b class="' + cc + '">' +
        (d.prev == null ? '—' : d.prev) + ' → ' + d.now +
        (dv == null ? '' : '（' + fmtDelta(dv) + '）') + '</b></div>';
    }).join('');
    const list = (arr, title, c2) => (arr && arr.length)
      ? '<div style="margin-top:10px"><b class="' + c2 + '">' + title + '</b><ul class="clean">' +
        arr.map((x) => '<li>' + esc(typeof x === 'string' ? x : (x.dimension || '') + '：' + (x.problem || '')) + '</li>').join('') +
        '</ul></div>' : '';
    cmpHTML = '<div class="cmp-banner"><b>与上一版 v' + cmp.prev_seq + ' 对比：</b>' +
      '<span class="' + cls + '">' + fmtDelta(dt) + ' 分</span>（' + cmp.prev_total + ' → ' + r.total + '）' +
      (cmp.comment ? '<div style="margin-top:6px;color:#3d4a5c">' + esc(cmp.comment) + '</div>' : '') +
      '<div class="cmp-grid">' + cells + '</div>' +
      list(cmp.fixed_llm || cmp.fixed, '已修复', 'up') +
      list(cmp.remaining_llm || cmp.remaining, '仍未修复', 'down') +
      list(cmp.new_issues, '新出现的问题', 'down') +
      (cmp.advice ? '<div style="margin-top:10px;color:#0b7a44">下一步最该做的一件事：' + esc(cmp.advice) + '</div>' : '') +
      '</div>';
  }

  const pii = (r.guard && r.guard.pii) || [];
  const piiHTML = pii.length
    ? '<div class="guard-banner">⚠ 隐私提醒：' + pii.map((p) =>
      esc(p.type + '（' + p.snippet + '）— ' + p.advice)).join('；') + '</div>'
    : '';

  const dimHTML = (r.dims || []).map((d) => {
    const rate = Math.round((d.score / d.max) * 100);
    return '<div class="dim"><div class="dim-top"><span class="dim-name">' + esc(d.name) +
      '</span><span class="dim-score" style="color:' + scoreColor(rate) + '">' + d.score + ' / ' + d.max + '</span></div>' +
      '<div class="dim-bar"><i style="width:' + rate + '%;background:' + scoreColor(rate) + '"></i></div>' +
      (d.reason ? '<div class="dim-reason">' + esc(d.reason) + '</div>' : '') +
      (d.evidence && d.evidence.length
        ? d.evidence.map((e) => '<div class="dim-ev">原文／证据：' + esc(e) + '</div>').join('') : '') +
      (d.fix ? '<div class="dim-fix">→ ' + esc(d.fix) + '</div>' : '') +
      refHTML(d.ref) + '</div>';
  }).join('');

  const dedHTML = (r.deduction_top3 || []).map((d, i) => {
    const key = 'ded_' + i;
    const on = !!state.adopted[r.version.id + ':' + key];
    return '<div class="ded"><h3>扣分点 ' + (i + 1) + '｜' + esc(d.dimension || '') + '</h3>' +
      '<div class="advice">' + esc(d.problem || '') + '</div>' +
      '<div class="advice" style="color:#0b7a44;margin-top:6px">怎么改：' + esc(d.advice || '') + '</div>' +
      (d.impact ? '<div class="impact">预期：' + esc(d.impact) + '</div>' : '') +
      refHTML(d.ref) +
      '<div class="adopt"><input type="checkbox" data-adopt="' + key + '" ' + (on ? 'checked' : '') + '>' +
      '<label>我已采纳这条建议（记入采纳看板）</label></div></div>';
  }).join('');

  const rwHTML = (r.rewrites || []).map((x, i) => {
    const risks = (x.fabrication_risks || []).map((rk) =>
      '<div class="rw-risk">⚠ ' + esc(rk.type) + '：' + esc((rk.items || []).join('、')) + ' — ' + esc(rk.advice) + '</div>').join('');
    const gaps = (x.gaps || []).length
      ? '<div class="rw-foot">需要你补充的数据：' + (x.gaps || []).map((g) =>
        '<span class="placeholder">' + esc(g) + '</span>').join('、') + '</div>' : '';
    const after = esc(x.rewritten).replace(/【请补充[^】]*】/g, (m) => '<span class="placeholder">' + m + '</span>');
    return '<div class="rw"><div class="sec">' + esc(x.section || '') +
      (x.reason ? '｜' + esc(x.reason) : '') + '</div>' +
      '<div class="rw-grid"><div class="rw-box rw-before"><div class="rw-lab">原文</div>' + esc(x.original) + '</div>' +
      '<div class="rw-box rw-after"><div class="rw-lab">改写参考稿（请确认真实性）</div>' + after + '</div></div>' +
      risks + gaps +
      '<div class="rw-foot"><button class="mini" data-copy="' + i + '">复制改写稿</button>' +
      '<span>复制后把占位符替换成你的真实数据</span></div></div>';
  }).join('');

  const riskHTML = (r.interview_risks || []).map((x) => '<li>' + esc(x) + '</li>').join('');
  const actHTML = (r.next_actions || []).map((x) => '<li>' + esc(x) + '</li>').join('');
  const m = r.metrics || {};
  const metricsHTML =
    '<div class="metric"><b>' + (m.chars || 0) + '</b><span>正文字数</span></div>' +
    '<div class="metric"><b>' + (m.est_pages || 0) + '</b><span>估算页数</span></div>' +
    '<div class="metric"><b>' + (m.quant_count_exp || 0) + '</b><span>经历中的量化数字</span></div>' +
    '<div class="metric"><b>' + (m.bullet_count || 0) + '</b><span>要点条数</span></div>' +
    '<div class="metric"><b>' + ((m.sections_found || []).length) + '</b><span>识别到的小节</span></div>' +
    '<div class="metric"><b>' + (m.self_eval_chars || 0) + '</b><span>自我评价字数</span></div>';

  const missHTML = (m.sections_missing || []).length
    ? '<div class="tagrow tight">' + m.sections_missing.map((s) =>
      '<span class="tag tag-warn">缺少：' + esc(s) + '</span>').join('') + '</div>'
    : '<div class="tagrow tight"><span class="tag tag-ok">核心小节齐全</span></div>';

  const hr = r.hr_score;
  const hrHTML = hr
    ? '<div class="card"><h2>人工评分 <span class="tag tag-ok">已录入</span></h2>' +
      '<div class="metrics"><div class="metric"><b>' + hr.total + '</b><span>人工总分</span></div>' +
      '<div class="metric"><b>' + (hr.total - r.total).toFixed(1) + '</b><span>人工 − Agent</span></div></div>' +
      '<p class="muted small">评分人：' + esc(hr.reviewer || '未填') + (hr.note ? '｜备注：' + esc(hr.note) : '') + '</p></div>'
    : '';

  host.innerHTML =
    engineBar + piiHTML +
    '<div class="card"><div class="score-hero">' +
      '<div style="position:relative">' + ringSVG(r.total) + '</div>' +
      '<div class="hero-txt">' +
        '<h2>' + esc(r.one_liner || '诊断完成') + '</h2>' +
        '<p class="muted">' + esc(r.summary || '') + '</p>' +
        '<div class="hero-tags">' +
          '<span class="tag ' + fitCls + '">JD 契合：' + esc(r.jd_fit.level) + '</span>' +
          '<span class="tag tag-info">目标岗位：' + esc(r.jd.title) + '</span>' +
          '<span class="tag">版本 v' + (v.seq || 1) + '</span>' +
          '<span class="tag">' + esc(v.created_at || '') + '</span>' +
          '<span class="tag ' + (offline ? 'tag-warn' : 'tag-ok') + '">' + esc(r.score_source || '') + '</span>' +
          gauge + '</div>' +
        (r.jd_fit.comment ? '<p class="muted small">' + esc(r.jd_fit.comment) + '</p>' : '') +
      '</div></div></div>' +
    clarifyHTML +
    cmpHTML +
    '<div class="card"><h2>六维度体检</h2><div class="dims">' +
      '<div><canvas id="radar"></canvas></div><div class="dim-list">' + dimHTML + '</div>' +
    '</div>' +
    '<div class="hint">每个维度下方的「依据」标签来自 <code>kb/rubric.md</code>，由分数反查档位生成，可点开看原文。</div></div>' +
    '<div class="card"><h2>Top 3 扣分点（按改进收益排序）</h2><div class="ded-list">' +
      (dedHTML || '<div class="empty">本次未给出扣分点</div>') + '</div></div>' +
    '<div class="card"><h2>改写参考稿</h2>' +
      ((r.rewrites || []).length ? rwHTML : '<div class="empty">本次没有生成改写</div>') +
      '<div class="hint">' + esc((r.guard || {}).note || '') + '</div></div>' +
    hrHTML +
    '<div class="cols2">' +
      '<div class="card"><h2>面试官可能追问的点</h2><ul class="clean">' + (riskHTML || '<li>无</li>') + '</ul></div>' +
      '<div class="card"><h2>下一步动作</h2><ul class="clean">' + (actHTML || '<li>无</li>') + '</ul></div>' +
    '</div>' +
    '<div class="card"><h2>客观指标（脚本统计，作为打分证据）</h2>' +
      '<div class="metrics">' + metricsHTML + '</div>' + missHTML + '</div>';

  const cv = host.querySelector('#radar');
  if (cv) drawRadar(cv, r.dims);

  host.querySelectorAll('[data-ref]').forEach((b) => {
    b.addEventListener('click', () => {
      const el = document.getElementById(b.dataset.ref);
      if (el) el.classList.toggle('hidden');
    });
  });

  host.querySelectorAll('[data-adopt]').forEach((cb) => {
    cb.addEventListener('change', async () => {
      const key = cb.dataset.adopt;
      const id = r.version.id;
      state.adopted[id + ':' + key] = cb.checked;
      try {
        await post('/api/adopt', { version_id: id, item_key: key, adopted: cb.checked });
        toast(cb.checked ? '已记录采纳，可在「⑤ 采纳看板」看到变化' : '已取消采纳');
      } catch (e) { toast('记录失败：' + e.message); }
    });
  });

  host.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const i = Number(btn.dataset.copy);
      const txt = (r.rewrites[i] || {}).rewritten || '';
      navigator.clipboard.writeText(txt).then(() => toast('已复制，记得替换占位符'))
        .catch(() => toast('复制失败，请手动选择'));
    });
  });
}

/* ---------------- 版本库 ---------------- */
$('#refresh-history').addEventListener('click', loadHistory);

async function loadHistory() {
  if (!state.userId) {
    $('#history-list').innerHTML = '<div class="empty">还没有历史版本，先去做一次诊断。</div>';
    return;
  }
  try {
    const r = await api('/api/versions?user_id=' + encodeURIComponent(state.userId));
    if (!r.versions.length) {
      $('#history-list').innerHTML = '<div class="empty">还没有历史版本。</div>';
      return;
    }
    $('#history-list').innerHTML = r.versions.map((v) => {
      const c = scoreColor(v.total);
      const hr = v.hr_score ? '<span class="tag tag-ok">人工 ' + v.hr_score.total + '</span>' : '';
      const eng = (v.engine === 'offline') ? '<span class="tag tag-warn">离线</span>' : '';
      return '<div class="hitem" data-vid="' + v.id + '">' +
        '<div class="hseq">v' + v.seq + '</div>' +
        '<div><div>' + esc(v.label || v.filename || '未命名') + ' · ' + esc(v.jd_title || '') + '</div>' +
        '<div class="hmeta">' + esc(v.created_at) + ' · 量化 ' + ((v.metrics || {}).quant_count_exp || 0) +
        ' 处 · ' + ((v.dims || []).length) + ' 个维度 ' + hr + eng + '</div></div>' +
        '<div class="htotal" style="color:' + c + '">' + v.total + '</div>' +
        '<button class="mini">查看</button></div>';
    }).join('');
    $$('.hitem').forEach((it) => it.addEventListener('click', () => loadVersion(it.dataset.vid)));
  } catch (e) {
    $('#history-list').innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>';
  }
}

async function loadVersion(vid, targetSel) {
  try {
    const v = await api('/api/version/' + vid + '?compare=1');
    const report = {
      user_id: state.userId, jd: v.jd_struct, dims: v.dims, total: v.total,
      one_liner: v.one_liner || ('v' + v.seq + ' · ' + (v.label || '')),
      summary: v.summary || '',
      jd_fit: v.jd_fit || { level: '基本匹配', comment: '' },
      deduction_top3: v.deductions, rewrites: v.rewrites, guard: v.guard,
      metrics: v.metrics, interview_risks: v.interview_risks || [],
      next_actions: v.next_actions || [],
      version: { id: v.id, seq: v.seq, created_at: v.created_at },
      compare: v.compare || null,
      engine: v.engine || 'llm', offline_reason: '',
      score_source: (v.engine === 'offline') ? '规则引擎（离线）' : '大模型语义判断',
      clarify: v.clarify || {},
      hr_score: v.hr_score || null,
    };
    state.adopted = {};
    Object.keys(v.adoptions || {}).forEach((k) => { state.adopted[v.id + ':' + k] = v.adoptions[k]; });
    const box = $(targetSel || '#history-detail');
    box.classList.remove('empty');
    box.innerHTML = '';
    renderReport(report, box);
  } catch (e) { toast('加载失败：' + e.message); }
}

/* ---------------- 校准看板 ---------------- */
$('#refresh-calib').addEventListener('click', loadCalib);

async function loadCalib() {
  if (!state.userId) {
    $('#calib-table').innerHTML = '<div class="empty">还没有诊断记录。</div>';
    return;
  }
  try {
    const [c, vs] = await Promise.all([
      api('/api/calibration?user_id=' + encodeURIComponent(state.userId)),
      api('/api/versions?user_id=' + encodeURIComponent(state.userId)),
    ]);
    state.calib = c;
    state.hrMap = {};
    (vs.versions || []).forEach((v) => { state.hrMap[v.id] = v; });

    $('#calib-metrics').innerHTML =
      metricCard('样本数', c.n, '已录入人工分的版本') +
      metricCard('MAE', c.mae == null ? '—' : c.mae, '平均绝对偏差，越小越准') +
      metricCard('±5 分命中率', c.hit5 == null ? '—' : Math.round(c.hit5 * 100) + '%', '偏差在 5 分内的占比') +
      metricCard('Pearson r', c.corr_ok ? c.corr : '样本不足', c.corr_ok ? '相关性，越接近 1 越好' : '需 ≥3 个样本');

    $('#calib-advice').innerHTML = (c.advice || []).map((a) => '<div>' + esc(a) + '</div>').join('');

    $('#calib-table').className = '';
    $('#calib-table').innerHTML = c.rows.length ? (
      '<table class="table"><thead><tr><th>版本</th><th>Agent</th><th>人工</th><th>偏差</th>' +
      '<th>±5 内</th><th>评分人</th><th>备注</th></tr></thead><tbody>' +
      c.rows.map((r) => '<tr><td>v' + r.seq + ' · ' + esc(r.label || '') +
        '<div class="hmeta">' + esc(r.created_at) + '</div></td>' +
        '<td class="num">' + r.agent + '</td><td class="num">' + r.human + '</td>' +
        '<td class="num" style="color:' + (Math.abs(r.diff) <= 5 ? '#0f9d58' : '#d92d20') + '">' + fmtDelta(r.diff) + '</td>' +
        '<td>' + (r.in5 ? '<span class="tag tag-ok">是</span>' : '<span class="tag tag-bad">否</span>') + '</td>' +
        '<td>' + esc(r.reviewer || '—') + '</td><td class="muted small">' + esc(r.note || '—') + '</td></tr>').join('') +
      '</tbody></table>') : '<div class="empty">还没有人工评分记录，先在最下面给某个版本打分。</div>';

    renderDimBias(c.dim_bias || []);

    const sel = $('#hr-version');
    sel.innerHTML = (vs.versions || []).map((v) =>
      '<option value="' + v.id + '">v' + v.seq + ' · ' + esc(v.label || v.filename || '') +
      '（Agent ' + v.total + '）</option>').join('');
    $('#hr-dims').innerHTML = DIMS.map(([k, n, mx]) =>
      '<label class="sel">' + esc(n) + ' <input type="number" min="0" max="' + mx +
      '" step="1" data-hrdim="' + k + '" placeholder="/' + mx + '" /></label>').join('');
    prefillHr();
  } catch (e) {
    $('#calib-table').innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>';
  }
}

function metricCard(label, value, sub) {
  return '<div class="metric"><b>' + esc(value) + '</b><span>' + esc(label) +
    (sub ? '<br><span style="font-size:11px;color:#93a1b3">' + esc(sub) + '</span>' : '') + '</span></div>';
}

function renderDimBias(list) {
  const box = $('#calib-dims');
  const has = list.some((d) => d.bias != null);
  if (!has) {
    box.className = 'empty';
    box.textContent = '需要录入"逐维度人工分"才能分析（在上面的表单里填维度分）。';
    return;
  }
  box.className = '';
  const maxAbs = Math.max(5, ...list.map((d) => Math.abs(d.bias || 0)));
  box.innerHTML = list.map((d) => {
    const v = d.bias;
    if (v == null) {
      return '<div class="bias-row"><div class="bias-name">' + esc(d.name) +
        '</div><div class="bias-track"></div><div class="bias-val muted">无数据</div></div>';
    }
    const w = Math.min(50, (Math.abs(v) / maxAbs) * 50);
    const col = Math.abs(v) > 2 ? (v > 0 ? '#d92d20' : '#2563eb') : '#93a1b3';
    const left = v >= 0 ? 50 : 50 - w;
    return '<div class="bias-row"><div class="bias-name">' + esc(d.name) + '</div>' +
      '<div class="bias-track"><div class="bias-zero"></div>' +
      '<div class="bias-fill" style="left:' + left + '%;width:' + w + '%;background:' + col + '"></div></div>' +
      '<div class="bias-val" style="color:' + col + '">' + fmtDelta(v) + '</div></div>';
  }).join('');
}

function prefillHr() {
  const id = Number($('#hr-version').value);
  const v = state.hrMap[id];
  if (!v) return;
  const hs = v.hr_score;
  $('#hr-total').value = hs ? hs.total : '';
  $('#hr-total').placeholder = 'Agent 给了 ' + v.total;
  $('#hr-reviewer').value = hs ? (hs.reviewer || '') : '';
  $('#hr-note').value = hs ? (hs.note || '') : '';
  $$('[data-hrdim]').forEach((inp) => {
    const k = inp.dataset.hrdim;
    const d = (v.dims || []).find((x) => x.key === k);
    inp.placeholder = d ? ('Agent ' + d.score) : '';
    inp.value = (hs && hs.dims && hs.dims[k] != null) ? hs.dims[k] : '';
  });
}
$('#hr-version').addEventListener('change', prefillHr);

$('#hr-save').addEventListener('click', async () => {
  const vid = Number($('#hr-version').value);
  const total = $('#hr-total').value;
  if (!vid || total === '') { toast('请选择版本并填写人工总分'); return; }
  const dims = {};
  $$('[data-hrdim]').forEach((i) => { if (i.value !== '') dims[i.dataset.hrdim] = Number(i.value); });
  try {
    await post('/api/hr_score', {
      version_id: vid, total: Number(total), dims: dims,
      reviewer: $('#hr-reviewer').value.trim(), note: $('#hr-note').value.trim(),
    });
    toast('已保存人工评分');
    loadCalib();
  } catch (e) { toast('保存失败：' + e.message); }
});

/* ---------------- 采纳看板 ---------------- */
$('#refresh-adopt').addEventListener('click', loadAdopt);

async function loadAdopt() {
  if (!state.userId) {
    $('#adopt-metrics').innerHTML = '<div class="empty">还没有诊断记录。</div>';
    return;
  }
  try {
    const b = await api('/api/adoption_board?user_id=' + encodeURIComponent(state.userId));
    const lift = b.lift || {};
    $('#adopt-metrics').innerHTML =
      metricCard('采纳率', b.rate == null ? '—' : Math.round(b.rate * 100) + '%', '已采纳 / 全部建议') +
      metricCard('建议条目', b.items_total, '累计扣分点条数') +
      metricCard('已采纳', b.items_adopted, '勾选"我已采纳"的条数') +
      metricCard('采纳后均提分', lift.with_adopt_avg == null ? '—' : fmtDelta(lift.with_adopt_avg),
        lift.no_adopt_avg == null ? '需至少两版' : ('未采纳组 ' + fmtDelta(lift.no_adopt_avg)));

    $('#adopt-advice').innerHTML = (b.advice || []).map((a) => '<div>' + esc(a) + '</div>').join('');

    const pv = b.per_version || [];
    $('#adopt-bars').innerHTML = pv.length ? pv.map((v) => {
      const pct = v.items ? Math.round((v.adopted / v.items) * 100) : 0;
      return '<div class="ab-row"><div class="ab-name">v' + v.seq + '</div>' +
        '<div class="ab-track"><div class="ab-fill" style="width:' + pct + '%"></div>' +
        '<div class="ab-rest" style="width:' + (100 - pct) + '%"></div></div>' +
        '<div class="ab-val">' + v.adopted + '/' + v.items + '（' + pct + '%）</div></div>';
    }).join('') : '<div class="empty">还没有版本记录。</div>';

    const rows = b.lift_rows || [];
    $('#adopt-lift').innerHTML = rows.length ? rows.map((r) => {
      const c = r.delta > 0 ? '#0f9d58' : (r.delta < 0 ? '#d92d20' : '#6c7a8c');
      return '<div class="lift-card"><div>v' + r.from_seq + ' → v' + r.to_seq +
        '<div class="hmeta">采纳 ' + r.adopted + ' 条建议</div></div>' +
        '<b style="color:' + c + '">' + fmtDelta(r.delta) + ' 分</b></div>';
    }).join('') : '<div class="empty">至少需要两个版本，且上一版勾选过"我已采纳"，才会出现提分对比。</div>';
  } catch (e) {
    $('#adopt-metrics').innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>';
  }
}

/* ---------------- 演示数据 ---------------- */
$$('.seed-btn').forEach((b) => b.addEventListener('click', async () => {
  b.disabled = true;
  const old = b.textContent;
  b.textContent = '生成中…';
  try {
    const r = await post('/api/demo_seed', { user_id: state.userId || null });
    state.userId = r.user_id;
    localStorage.setItem('rd_user', r.user_id);
    $('#pill-user').textContent = '用户 ' + r.user_id;
    toast(r.seeded ? '已生成演示数据：2 个版本 + 人工分 + 采纳记录' : '演示数据已存在，直接查看');
    loadCalib(); loadAdopt(); loadHistory();
  } catch (e) {
    toast('生成失败：' + e.message);
  } finally {
    b.disabled = false;
    b.textContent = old;
  }
}));

/* ---------------- boot ---------------- */
if (location.protocol === 'file:') {
  const b = document.createElement('div');
  b.className = 'guard-banner';
  b.style.margin = '10px 24px';
  b.textContent = '当前是直接双击 HTML 打开的，浏览器无法调用本地服务。请关掉本页，双击项目根目录的「启动.bat」，等窗口出现后再访问 http://127.0.0.1:8765';
  document.body.prepend(b);
}
health();
setInterval(health, 60000);

(function deepLink() {
  const params = new URLSearchParams(location.search);
  const uid = params.get('user');
  if (uid) { state.userId = uid; localStorage.setItem('rd_user', uid); }
  if (params.get('tab')) { setTab(params.get('tab')); return; }
  const v = params.get('v');
  if (!v) return;
  $('#report-empty').classList.add('hidden');
  $('#report-body').classList.remove('hidden');
  setTab('report');
  loadVersion(v, '#report-body');
})();

'use strict';
/* SEELS review UI frontend - vanilla JS, no external dependencies. */

var state = {
  papers: [], paperId: null, paperInfo: null,
  page: 1, dpi: 140, showEv: true,
  measurements: [], samples: [], selectedM: null,
  pageEvidence: [],
  curves: [], curveId: null, curve: null,
  pickMode: null,           // null | 'x1' | 'x2' | 'y1' | 'y2' | 'point'
  calDraft: { x1: null, x2: null, y1: null, y2: null },
  flags: [], reviews: []
};

function $(id) { return document.getElementById(id); }
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function toast(msg, ms) {
  var t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(function () { t.classList.add('hidden'); }, ms || 4200);
}
function reviewer() {
  var v = ($('reviewer').value || 'anonymous').trim();
  try { localStorage.setItem('seels_reviewer', v); } catch (e) {}
  return v;
}
async function api(path, opts) {
  var r = await fetch(path, opts);
  var ct = r.headers.get('content-type') || '';
  if (!r.ok) {
    var msg = '';
    try { msg = ct.indexOf('json') >= 0 ? (await r.json()).error : await r.text(); } catch (e) {}
    throw new Error(r.status + ' ' + (msg || r.statusText));
  }
  return ct.indexOf('json') >= 0 ? r.json() : r.blob();
}
async function apiPost(path, obj) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                     body: JSON.stringify(obj || {}) });
}
function fmtNum(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0)) return v.toExponential(3);
    return String(Math.round(v * 1e6) / 1e6);
  }
  return String(v);
}
function statusBadge(s) { return '<span class="badge ' + esc(s) + '">' + esc(s || '-') + '</span>'; }

/* ------------------------------------------------------------- papers */
async function loadPapers() {
  state.papers = await api('/api/papers');
  var sel = $('paperSel');
  sel.innerHTML = '';
  state.papers.forEach(function (p) {
    var o = document.createElement('option');
    o.value = p.paper_id;
    var label = p.title ? p.title.slice(0, 48) : (p.source_path.split(/[\\/]/).pop() || p.paper_id);
    o.textContent = label + ' [' + p.summary.n_measurements + 'M/' + p.summary.n_evidence + 'E]';
    sel.appendChild(o);
  });
  if (state.papers.length && !state.paperId) selectPaper(state.papers[0].paper_id);
}
async function selectPaper(pid) {
  var seq = (state.loadSeq = (state.loadSeq || 0) + 1);
  state.paperId = pid;
  $('paperSel').value = pid;
  $('mTable').querySelector('tbody').innerHTML = '';   // clear stale rows from previous paper
  state.paperInfo = await api('/api/paper?id=' + encodeURIComponent(pid));
  if (seq !== state.loadSeq) return;                   // a newer selection took over
  var p = state.paperInfo.paper;
  var s = state.paperInfo.summary;
  $('pgTotal').textContent = String(p.n_pages || '?');
  $('paperInfo').innerHTML =
    esc((p.title || '(无标题)')) + ' — ' + esc(p.pdf_kind) + ' · ' + (p.n_pages || '?') + '页 · 解析:' +
    esc(p.parse_status) + ' · 测量:' + s.n_measurements + ' · 样品:' + s.n_samples +
    ' · 证据:' + s.n_evidence + ' · 曲线:' + s.n_curves + ' · 未决QC:' + s.n_open_flags +
    ' · sha256:' + esc((p.sha256 || '').slice(0, 12)) + '…';
  state.page = 1;
  $('pgNum').value = '1';
  await Promise.all([loadMeasurements(), loadSamples(), loadCurves(), loadFlags(), loadReviews()]);
  if (seq !== state.loadSeq) return;
  showPage(1);
}

/* ------------------------------------------------------------- viewer */
function currentPageMeta() {
  var pages = (state.paperInfo && state.paperInfo.pages) || [];
  return pages[state.page - 1] || null;
}
function showPage(n) {
  if (!state.paperInfo || !state.paperInfo.paper) return;   // paper still loading: ignore
  var total = state.paperInfo.paper.n_pages || 1;
  n = Math.max(1, Math.min(total, n | 0));
  state.page = n;
  $('pgNum').value = String(n);
  var img = $('pageImg');
  img.src = '/api/page_image?paper_id=' + encodeURIComponent(state.paperId) +
            '&page=' + n + '&dpi=' + state.dpi + '&t=' + Date.now();
  var meta = currentPageMeta();
  $('pageMeta').textContent = meta ?
    ('状态:' + meta.status + ' 字符:' + meta.char_count + ' 表:' + meta.n_tables +
     ' 图注:' + meta.n_captions + ' ' + Math.round(meta.width_pts) + '×' + Math.round(meta.height_pts) + 'pt') : '';
  img.onload = renderOverlay;
}
var EV_COLORS = { text: '#2563eb', table_cell: '#16a34a', caption: '#d97706',
                  figure: '#7c3aed', table: '#0891b2', curve: '#db2777' };
async function renderOverlay() {
  var svg = $('evOverlay');
  svg.innerHTML = '';
  if (!state.showEv || !state.paperId) return;
  var meta = currentPageMeta();
  if (!meta) return;
  svg.setAttribute('viewBox', '0 0 ' + meta.width_pts + ' ' + meta.height_pts);
  var evs = await api('/api/page_evidence?paper_id=' + encodeURIComponent(state.paperId) +
                      '&page=' + state.page);
  state.pageEvidence = evs;
  evs.forEach(function (ev) {
    if (!ev.bbox || ev.bbox.length !== 4) return;
    var r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    var b = ev.bbox;
    r.setAttribute('x', b[0]); r.setAttribute('y', b[1]);
    r.setAttribute('width', Math.max(1, b[2] - b[0]));
    r.setAttribute('height', Math.max(1, b[3] - b[1]));
    var col = EV_COLORS[ev.element_kind] || '#2563eb';
    r.setAttribute('stroke', col); r.setAttribute('fill', col);
    r.dataset.evidenceId = ev.evidence_id;
    r.addEventListener('mousemove', function (e) {
      var t = $('tooltip');
      t.innerHTML = '<b>' + esc(ev.element_kind) + '</b> ' + esc(ev.element_ref || '') +
        '<br>p' + ev.page + ' bbox[' + b.map(function (x) { return Math.round(x); }).join(', ') + ']' +
        '<br>“' + esc((ev.quote_text || '').slice(0, 160)) + '”' +
        '<br><span style="color:#9fb0c8">方法:' + esc(ev.extraction_method) +
        ' · 审核:' + esc(ev.review_status) + '</span>';
      t.classList.remove('hidden');
      t.style.left = Math.min(window.innerWidth - 400, e.clientX + 14) + 'px';
      t.style.top = (e.clientY + 12) + 'px';
    });
    r.addEventListener('mouseleave', function () { $('tooltip').classList.add('hidden'); });
    r.addEventListener('click', async function () {
      var ms = await api('/api/measurements_by_evidence?evidence_id=' + ev.evidence_id);
      if (ms && ms.length) selectMeasurement(ms[0].measurement_id, false);
      else toast('该证据未关联测量（可能属于样品字段或被拒条目）');
    });
    svg.appendChild(r);
  });
  highlightSelectedEvidence();
}
function highlightSelectedEvidence() {
  if (!state.selectedM) return;
  var ids = (state.selectedM.evidence || []).map(function (e) { return e.evidence_id; });
  var svg = $('evOverlay');
  Array.prototype.forEach.call(svg.querySelectorAll('rect'), function (r) {
    if (ids.indexOf(r.dataset.evidenceId) >= 0) r.classList.add('sel');
  });
}
function flashEvidence(evidenceId) {
  var svg = $('evOverlay');
  Array.prototype.forEach.call(svg.querySelectorAll('rect'), function (r) {
    r.classList.remove('flash');
    if (r.dataset.evidenceId === evidenceId) {
      r.classList.add('flash');
      r.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
}

/* ------------------------------------------------------------- data tab */
async function loadSamples() {
  state.samples = await api('/api/samples?paper_id=' + encodeURIComponent(state.paperId));
  var box = $('samplesBox');
  if (!state.samples.length) { box.innerHTML = '<span class="dim">（未识别出样品）</span>'; return; }
  box.innerHTML = '<b>样品：</b>' + state.samples.slice(0, 40).map(function (s) {
    var f = ((s.fields || {}).identity || {}).formula;
    var formula = f && f.value ? ' (' + esc(f.value) + ')' : '';
    return '<span class="sample-chip" title="' + esc(s.sample_id) + '">' +
           esc(s.name || '?') + formula + '</span>';
  }).join('');
}
async function loadMeasurements() {
  var qs = '/api/measurements?paper_id=' + encodeURIComponent(state.paperId) + '&limit=1000';
  var st = $('mStatus').value, q = $('mSearch').value.trim();
  if (st) qs += '&status=' + encodeURIComponent(st);
  if (q) qs += '&q=' + encodeURIComponent(q);
  state.measurements = await api(qs);
  var tb = $('mTable').querySelector('tbody');
  tb.innerHTML = '';
  var smp = {};
  state.samples.forEach(function (s) { smp[s.sample_id] = s.name; });
  state.measurements.forEach(function (m) {
    var tr = document.createElement('tr');
    tr.className = 'clickable';
    tr.dataset.mid = m.measurement_id;
    tr.innerHTML =
      '<td>' + esc(m.quantity || '(未定)') + '</td>' +
      '<td>' + esc(m.value_raw) + ' ' + esc(m.unit_raw || '') + '</td>' +
      '<td>' + (m.value_std === null || m.value_std === undefined
                ? '<span class="dim">null:' + esc(m.std_null_reason || '?') + '</span>'
                : fmtNum(m.value_std) + ' ' + esc(m.unit_std || '')) + '</td>' +
      '<td>' + esc(m.sample_id ? (smp[m.sample_id] || m.sample_id) : '—') + '</td>' +
      '<td>' + esc(m.source_type) + '</td>' +
      '<td>' + esc(m.extraction_method) + '</td>' +
      '<td>' + (m.n_evidence || 0) + '</td>' +
      '<td>' + statusBadge(m.status) + '</td>';
    tr.addEventListener('click', function () { selectMeasurement(m.measurement_id, true); });
    tb.appendChild(tr);
  });
  Array.prototype.forEach.call(tb.querySelectorAll('tr'), function (tr) {
    if (state.selectedM && tr.dataset.mid === state.selectedM.measurement_id) tr.classList.add('sel');
  });
}

/* ------------------------------------------------------------- detail */
async function selectMeasurement(mid, jump) {
  var m = await api('/api/measurement?id=' + encodeURIComponent(mid));
  state.selectedM = m;
  Array.prototype.forEach.call(document.querySelectorAll('#mTable tbody tr'), function (tr) {
    tr.classList.toggle('sel', tr.dataset.mid === mid);
  });
  renderDetail(m);
  renderCard(m.card);
  switchTab('detail');
  if (jump && m.evidence && m.evidence.length) {
    var ev = m.evidence[0];
    if (ev.page !== state.page) showPage(ev.page);
    setTimeout(function () { renderOverlay().then(function () { flashEvidence(ev.evidence_id); }); }, 260);
  }
}
function evHtml(ev) {
  return '<div class="ev-item">' +
    '<div><span class="badge ' + esc(ev.element_kind) + '">' + esc(ev.element_kind) + '</span> ' +
    esc(ev.element_ref || '') + ' <span class="dim">p' + ev.page +
    ' bbox[' + (ev.bbox || []).map(function (x) { return Math.round(x); }).join(', ') + ']' +
    ' · ' + esc(ev.source_type) + ' · ' + esc(ev.extraction_method) +
    ' · 审核:' + esc(ev.review_status) + (ev.role ? ' · 角色:' + esc(ev.role) : '') + '</span></div>' +
    '<div class="ev-quote">“' + esc(ev.quote_text) + '”</div>' +
    '<button data-jump="' + esc(ev.evidence_id) + '" data-page="' + ev.page + '">定位到原文</button>' +
    '</div>';
}
function navMeasurement(delta) {
  if (!state.measurements.length) return;
  var idx = -1;
  for (var i = 0; i < state.measurements.length; i++) {
    if (state.selectedM && state.measurements[i].measurement_id === state.selectedM.measurement_id) { idx = i; break; }
  }
  var next = idx < 0 ? (delta > 0 ? 0 : state.measurements.length - 1)
                     : (idx + delta + state.measurements.length) % state.measurements.length;
  selectMeasurement(state.measurements[next].measurement_id, true);
}
function renderDetail(m) {
  var pos = 0;
  for (var i = 0; i < state.measurements.length; i++) {
    if (state.measurements[i].measurement_id === m.measurement_id) { pos = i + 1; break; }
  }
  var cond = Object.keys(m.conditions || {}).map(function (k) {
    var v = m.conditions[k];
    if (!v || typeof v !== 'object') return esc(k) + ': ' + esc(v);
    if (v.value === null || v.value === undefined)
      return esc(k) + ': <span class="dim">null(' + esc(v.null_reason || '?') + ') raw=' + esc(v.raw || '') + '</span>';
    return esc(k) + ': ' + esc(v.value) + ' ' + esc(v.unit || '') + (v.raw ? ' <span class="dim">(' + esc(v.raw) + ')</span>' : '');
  }).join('<br>') || '<span class="dim">（无条件记录）</span>';
  $('detailBox').innerHTML =
    '<div class="actions" style="margin:0 0 6px">' +
      '<button id="btnPrevM" title="上一条 (↑/k)">⟨ 上一条</button>' +
      '<span class="dim">' + (pos ? pos + ' / ' + state.measurements.length : '') + '</span>' +
      '<button id="btnNextM" title="下一条 (↓/j)">下一条 ⟩</button>' +
    '</div>' +
    '<div class="detail-grid">' +
    '<span class="k">measurement_id</span><span>' + esc(m.measurement_id) + '</span>' +
    '<span class="k">quantity</span><span>' + esc(m.quantity || '(未定)') + '</span>' +
    '<span class="k">原始值 (value_raw)</span><span><b>' + esc(m.value_raw) + '</b> ' + esc(m.unit_raw || '') + '</span>' +
    '<span class="k">标准化 (value_std)</span><span>' +
      (m.value_std === null || m.value_std === undefined
        ? '<b>null</b> <span class="dim">原因: ' + esc(m.std_null_reason || '?') + '</span>'
        : '<b>' + fmtNum(m.value_std) + '</b> ' + esc(m.unit_std || '') +
          ' <span class="dim">via ' + esc(m.std_method || '') + '</span>') + '</span>' +
    '<span class="k">样品</span><span>' + (m.sample ? esc(m.sample.name) : '<span class="dim">未关联</span>') + '</span>' +
    '<span class="k">条件</span><span>' + cond + '</span>' +
    '<span class="k">测试方法</span><span>' + esc(m.test_method || '—') + '</span>' +
    '<span class="k">来源类型 / 抽取方法</span><span>' + esc(m.source_type) + ' / ' + esc(m.extraction_method) + '</span>' +
    '<span class="k">状态</span><span>' + statusBadge(m.status) + '</span>' +
    '<span class="k">运行</span><span class="dim">' + esc(m.run_id || '') + '</span>' +
    '</div>' +
    '<h3>证据（' + (m.evidence || []).length + '）</h3>' +
    (m.evidence || []).map(evHtml).join('') +
    '<div class="actions">' +
      '<button class="ok" data-act="accept">接受</button>' +
      '<button data-act="modify">修改…</button>' +
      '<button class="bad" data-act="reject">驳回</button>' +
      '<button class="warn" data-act="flag_uncertain">标记存疑</button>' +
      '<button data-act="back_to_evidence">返回原始证据</button>' +
      '<input type="text" id="revComment" placeholder="复核备注（随操作持久保存）">' +
    '</div>' +
    '<div class="modify-form" id="modifyForm">' +
      '<div class="row">quantity <input id="mfQty" size="18" value="' + esc(m.quantity || '') + '">' +
      ' value_raw <input id="mfVal" size="10" value="' + esc(m.value_raw || '') + '">' +
      ' unit_raw <input id="mfUnit" size="8" value="' + esc(m.unit_raw || '') + '"></div>' +
      '<div class="row">test_method <input id="mfMethod" size="24" value="' + esc(m.test_method || '') + '">' +
      '<button class="ok" id="mfSave">保存修改并接受</button> <span class="dim">修改后标准化值将自动重算；原值保留在审核记录中</span></div>' +
    '</div>' +
    '<h3>该测量的质量标记</h3>' +
    ((m.flags || []).length ? m.flags.map(flagHtml).join('') : '<span class="dim">无</span>') +
    '<h3>审核历史</h3>' +
    ((m.reviews || []).length ? reviewsTable(m.reviews) : '<span class="dim">尚无人工操作</span>');
  // wire events
  Array.prototype.forEach.call($('detailBox').querySelectorAll('[data-jump]'), function (b) {
    b.addEventListener('click', function () {
      var pg = parseInt(b.dataset.page, 10);
      if (pg !== state.page) showPage(pg);
      setTimeout(function () { renderOverlay().then(function () { flashEvidence(b.dataset.jump); }); }, 260);
    });
  });
  Array.prototype.forEach.call($('detailBox').querySelectorAll('[data-act]'), function (b) {
    b.addEventListener('click', function () { doReview(b.dataset.act); });
  });
  var bp = $('btnPrevM'), bn = $('btnNextM');
  if (bp) bp.addEventListener('click', function () { navMeasurement(-1); });
  if (bn) bn.addEventListener('click', function () { navMeasurement(+1); });
  var mf = $('modifyForm');
  if (mf) mf.classList.remove('open');
}
async function doReview(action) {
  var m = state.selectedM;
  if (!m) return;
  var comment = ($('revComment') || {}).value || '';
  var payload = { target_type: 'measurement', target_id: m.measurement_id,
                  action: action, comment: comment, reviewer: reviewer() };
  if (action === 'modify') {
    var mf = $('modifyForm');
    if (!mf.classList.contains('open')) { mf.classList.add('open'); return; }
    payload.new_value = { quantity: $('mfQty').value, value_raw: $('mfVal').value,
                          unit_raw: $('mfUnit').value, test_method: $('mfMethod').value };
  }
  if (action === 'back_to_evidence') {
    var ev = (m.evidence || [])[0];
    if (ev) { if (ev.page !== state.page) showPage(ev.page);
      setTimeout(function () { renderOverlay().then(function () { flashEvidence(ev.evidence_id); }); }, 260); }
  }
  try {
    var r = await apiPost('/api/review', payload);
    toast('已记录审核操作 #' + r.review_id + '（' + action + '，' + reviewer() + '）');
    await selectMeasurement(m.measurement_id, false);
    await Promise.all([loadMeasurements(), loadFlags(), loadReviews()]);
    if (action !== 'back_to_evidence' && action !== 'modify') {}
    if ($('revComment')) $('revComment').value = '';
  } catch (e) { toast('审核失败: ' + e.message); }
}

/* ------------------------------------------------------------- card */
var DIM_LABELS = {
  research_question: '① 研究问题', sample_and_composition: '② 样品与组成',
  conditions_and_method: '③ 条件与方法', key_result: '④ 关键结果',
  reliability_and_limits: '⑤ 可靠性与局限', transferable_insight: '⑥ 可迁移洞见'
};
function renderCard(card) {
  var box = $('cardBox');
  if (!card) {
    box.innerHTML = '<span class="dim">该测量尚无学习卡。点“生成学习卡”或在复核后自动刷新。</span>';
    return;
  }
  var dims = Object.keys(DIM_LABELS).map(function (k) {
    var d = (card.dimensions || {})[k] || {};
    return '<div class="card-dim"><b>' + DIM_LABELS[k] + '</b><br>' +
      (d.content ? esc(d.content) : '<span class="dim">（空）</span>') +
      (d.null_reason ? '<br><span class="nullr">缺失原因: ' + esc(d.null_reason) + '</span>' : '') +
      (d.evidence_ids && d.evidence_ids.length
        ? '<br><span class="dim">证据: ' + d.evidence_ids.map(esc).join(', ') + '</span>' : '') +
      '</div>';
  }).join('');
  box.innerHTML = '<div class="dim">AI解释（' + esc(card.ai_source) + '，更新于 ' + esc(card.updated_at) + '）</div>' +
    '<p>' + esc(card.ai_explanation || '') + '</p>' + dims +
    '<div class="dim">卡片内容由已入库证据模板化生成；接入 LLM 后 ai_source 将标注模型名，事实字段始终引用证据ID。</div>';
}

/* ------------------------------------------------------------- flags */
function flagHtml(f) {
  return '<div class="flag-item">' + statusBadge(f.severity) +
    '<span class="badge">' + esc(f.check_code) + '</span>' +
    '<span class="msg">' + esc(f.message) +
    ' <span class="dim">[' + esc(f.target_type) + ':' + esc(String(f.target_id).slice(0, 40)) + ']</span></span>' +
    (f.resolved ? '<span class="dim">已解决</span>'
                : '<button data-resolve="' + f.flag_id + '">标记解决</button>') +
    '</div>';
}
async function loadFlags() {
  state.flags = await api('/api/flags?paper_id=' + encodeURIComponent(state.paperId) + '&resolved=0');
  var n = state.flags.length;
  $('flagCount').textContent = String(n);
  var qc = state.flags.filter(function (f) { return f.check_code !== 'EX_NULL_FIELD'; });
  var nul = state.flags.filter(function (f) { return f.check_code === 'EX_NULL_FIELD'; });
  $('flagList').innerHTML = qc.length ? qc.map(flagHtml).join('') : '<span class="dim">无未决QC标记</span>';
  $('nullList').innerHTML = nul.length
    ? nul.slice(0, 200).map(function (f) {
        var d = f.details || {};
        return '<div class="flag-item"><span class="badge info">null</span><span class="msg">' +
          esc(f.message) + (d.page ? ' <span class="dim">p' + d.page + '</span>' : '') +
          (d.quote ? ' <span class="dim">“' + esc(String(d.quote).slice(0, 60)) + '”</span>' : '') +
          '</span></div>';
      }).join('')
    : '<span class="dim">无缺失字段记录</span>';
  Array.prototype.forEach.call(document.querySelectorAll('[data-resolve]'), function (b) {
    b.addEventListener('click', async function () {
      await apiPost('/api/flag_resolve', { flag_id: parseInt(b.dataset.resolve, 10), reviewer: reviewer() });
      loadFlags();
    });
  });
}

/* ------------------------------------------------------------- reviews */
function reviewsTable(rs) {
  return '<table class="grid"><thead><tr><th>时间</th><th>对象</th><th>动作</th><th>复核人</th><th>备注</th><th>变更</th></tr></thead><tbody>' +
    rs.map(function (r) {
      var chg = '';
      if (r.action === 'modify' && r.old_value && r.new_value) {
        chg = Object.keys(r.new_value).map(function (k) {
          return k + ': ' + esc(r.old_value ? r.old_value[k] : '') + '→' + esc(r.new_value[k]);
        }).join('; ');
      } else if (r.old_value && r.old_value.status !== undefined) {
        chg = 'status: ' + esc(r.old_value.status);
      } else if (r.old_value && r.old_value.verification_status !== undefined) {
        chg = 'verify: ' + esc(r.old_value.verification_status);
      }
      return '<tr><td class="dim">' + esc(r.created_at) + '</td><td>' + esc(r.target_type) +
        ':' + esc(String(r.target_id).slice(0, 18)) + '</td><td><b>' + esc(r.action) + '</b></td>' +
        '<td>' + esc(r.reviewer) + '</td><td>' + esc(r.comment || '') + '</td>' +
        '<td class="dim">' + chg + '</td></tr>';
    }).join('') + '</tbody></table>';
}
async function loadReviews() {
  state.reviews = await api('/api/reviews?paper_id=' + encodeURIComponent(state.paperId));
  $('revTable').outerHTML = reviewsTable(state.reviews).replace('<table class="grid">', '<table class="grid" id="revTable">');
}

/* ------------------------------------------------------------- curves */
async function loadCurves() {
  state.curves = await api('/api/curves?paper_id=' + encodeURIComponent(state.paperId));
  var sel = $('curveSel');
  sel.innerHTML = '';
  state.curves.forEach(function (c) {
    var o = document.createElement('option');
    o.value = c.curve_id;
    o.textContent = 'p' + c.page + ' ' + (c.figure_ref || '') + ' [' + c.verification_status + ',' + c.n_points + '点]';
    sel.appendChild(o);
  });
  if (state.curves.length) {
    state.curveId = state.curves[0].curve_id;
    sel.value = state.curveId;
    await selectCurve(state.curveId);
  } else {
    state.curveId = null; state.curve = null;
    $('cvRegion').removeAttribute('src'); $('cvOverlay').removeAttribute('src');
    $('cvRedraw').removeAttribute('src');
    $('curveStatus').textContent = '-';
    $('curveMeta').textContent = '（未提取图区，点“提取图表区域”）';
  }
}
async function selectCurve(cid) {
  state.curveId = cid;
  state.curve = await api('/api/curve?id=' + encodeURIComponent(cid));
  var c = state.curve;
  var t = Date.now();
  setImg('cvRegion', '/api/curve_image?id=' + encodeURIComponent(cid) + '&kind=region&t=' + t);
  setImgOrPlaceholder('cvOverlay', c.has_overlay, '/api/curve_image?id=' + encodeURIComponent(cid) + '&kind=overlay&t=' + t);
  setImgOrPlaceholder('cvRedraw', c.has_redraw, '/api/curve_image?id=' + encodeURIComponent(cid) + '&kind=redraw&t=' + t);
  $('curveStatus').textContent = c.verification_status;
  $('curveStatus').className = 'badge ' + c.verification_status;
  $('curveMeta').textContent = 'p' + c.page + ' ' + (c.figure_ref || '') + ' · 方法:' + c.method +
    ' · 点数:' + (c.points || []).length + ' · 校准:' + (c.calibration ? '有' : '无') +
    (c.notes ? ' · ' + c.notes.slice(0, 80) : '');
  // prefill calibration draft
  var cal = c.calibration;
  state.calDraft = { x1: null, x2: null, y1: null, y2: null };
  if (cal && cal.axes) {
    $('xScale').value = cal.axes.x.scale || 'linear';
    $('yScale').value = cal.axes.y.scale || 'linear';
    $('xUnit').value = cal.axes.x.unit || '';
    $('yUnit').value = cal.axes.y.unit || '';
    ['x', 'y'].forEach(function (ax) {
      (cal.axes[ax].points || []).slice(0, 2).forEach(function (p, i) {
        var slot = ax + (i + 1);
        state.calDraft[slot] = { px: p.px, py: p.py };
        $('px_' + slot).textContent = '(' + Math.round(p.px) + ',' + Math.round(p.py) + ')';
        $('v_' + slot).value = p.value;
      });
    });
  }
}
function setImg(id, src) {
  var img = $(id);
  img.style.opacity = '1';
  img.onerror = function () { img.style.opacity = '0.15'; img.title = '尚未生成'; };
  img.src = src;
}
function setImgOrPlaceholder(id, exists, src) {
  var img = $(id);
  if (exists) { setImg(id, src); return; }
  img.removeAttribute('src');
  img.style.opacity = '0.15';
  img.title = '尚未生成：完成轴校准并添加数据点（或导入 WPD CSV）后自动出现';
}

/* ------------------------------------------------------------- tabs & init */
function switchTab(name) {
  Array.prototype.forEach.call(document.querySelectorAll('.tabs button'), function (b) {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  Array.prototype.forEach.call(document.querySelectorAll('.tabpane'), function (p) {
    p.classList.toggle('active', p.id === 'tab-' + name);
  });
}
function initEvents() {
  try { var rv = localStorage.getItem('seels_reviewer'); if (rv) $('reviewer').value = rv; } catch (e) {}
  $('paperSel').addEventListener('change', function () { selectPaper(this.value); });
  $('pgPrev').addEventListener('click', function () { showPage(state.page - 1); });
  $('pgNext').addEventListener('click', function () { showPage(state.page + 1); });
  $('pgNum').addEventListener('change', function () { showPage(parseInt(this.value, 10) || 1); });
  $('dpiSel').addEventListener('change', function () { state.dpi = parseInt(this.value, 10); showPage(state.page); });
  $('showEv').addEventListener('change', function () { state.showEv = this.checked; renderOverlay(); });
  $('btnMSearch').addEventListener('click', loadMeasurements);
  $('mSearch').addEventListener('keydown', function (e) { if (e.key === 'Enter') loadMeasurements(); });
  document.addEventListener('keydown', function (e) {
    var tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); navMeasurement(+1); }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); navMeasurement(-1); }
  });
  Array.prototype.forEach.call(document.querySelectorAll('.tabs button'), function (b) {
    b.addEventListener('click', function () { switchTab(b.dataset.tab); });
  });
  $('btnExtract').addEventListener('click', async function () {
    toast('正在运行 rules-v1 抽取…');
    try {
      var r = await apiPost('/api/extract', { paper_id: state.paperId, extractor: 'rules-v1' });
      toast('抽取完成: ' + JSON.stringify(r.stats.n_measurements) + ' 条测量, ' +
            r.stats.n_evidence_new + ' 条新证据, ' + r.stats.n_uncertain_flags + ' 条缺失字段记录');
      await Promise.all([loadMeasurements(), loadSamples(), loadFlags()]);
    } catch (e) { toast('抽取失败: ' + e.message); }
  });
  $('btnFigures').addEventListener('click', async function () {
    toast('正在检测图区…');
    try {
      var r = await apiPost('/api/figures', { paper_id: state.paperId });
      toast('图区提取完成: ' + r.n + ' 个候选曲线/图');
      await loadCurves();
    } catch (e) { toast('图区提取失败: ' + e.message); }
  });
  $('btnQC').addEventListener('click', async function () {
    toast('正在运行 7 项质量检查…');
    try {
      var r = await apiPost('/api/qc', { paper_id: state.paperId });
      toast('QC 完成: 新增标记 ' + r.n_flags_new + '，需人工复核测量 ' +
            r.measurements_needing_human_review + ' 条');
      await Promise.all([loadFlags(), loadMeasurements()]);
    } catch (e) { toast('QC 失败: ' + e.message); }
  });
  $('btnCards').addEventListener('click', async function () {
    try {
      var r = await apiPost('/api/cards', { paper_id: state.paperId });
      toast('已生成/刷新 ' + r.cards + ' 张六维学习卡');
      if (state.selectedM) selectMeasurement(state.selectedM.measurement_id, false);
    } catch (e) { toast('学习卡生成失败: ' + e.message); }
  });
  $('btnCsv').addEventListener('click', async function () {
    try {
      var r = await api('/api/export?fmt=csv&paper_id=' + encodeURIComponent(state.paperId));
      $('exportBox').innerHTML = '已导出: ' + r.files.map(function (f) {
        return '<a href="/api/download?path=' + encodeURIComponent(f) + '" target="_blank">' +
               esc(f.split(/[\\/]/).pop()) + '</a>';
      }).join(' · ');
      toast('CSV 导出完成（' + r.files.length + ' 个文件）');
    } catch (e) { toast('导出失败: ' + e.message); }
  });
  $('btnXlsx').addEventListener('click', async function () {
    try {
      var r = await api('/api/export?fmt=xlsx&paper_id=' + encodeURIComponent(state.paperId));
      $('exportBox').innerHTML = '已导出: ' + r.files.map(function (f) {
        return '<a href="/api/download?path=' + encodeURIComponent(f) + '" target="_blank">' +
               esc(f.split(/[\\/]/).pop()) + '</a>';
      }).join(' · ');
      toast('Excel 导出完成');
    } catch (e) { toast('导出失败: ' + e.message); }
  });
  // ---- curves ----
  $('curveSel').addEventListener('change', function () { selectCurve(this.value); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-pick]'), function (b) {
    b.addEventListener('click', function () {
      state.pickMode = b.dataset.pick;
      $('pickHint').textContent = '拾取模式: 在 region 图上点击 ' + b.dataset.pick.toUpperCase() + ' 的已知刻度位置';
    });
  });
  $('btnPickPts').addEventListener('click', function () {
    if (!state.curve || !state.curve.calibration) { toast('请先完成坐标轴校准'); return; }
    state.pickMode = 'point';
    $('pickHint').textContent = '点取模式: 在 region 图上沿曲线点击添加数据点（每点即换算并入库）';
  });
  $('btnClearPick').addEventListener('click', function () {
    state.pickMode = null; $('pickHint').textContent = '';
  });
  $('cvRegion').addEventListener('click', async function (e) {
    if (!state.pickMode || !state.curveId) return;
    var img = $('cvRegion');
    var rect = img.getBoundingClientRect();
    var px = (e.clientX - rect.left) / rect.width * img.naturalWidth;
    var py = (e.clientY - rect.top) / rect.height * img.naturalHeight;
    if (state.pickMode === 'point') {
      try {
        var r = await apiPost('/api/curve_points', { curve_id: state.curveId, points: [[px, py]] });
        $('curveMsg').textContent = '已添加点 (' + px.toFixed(1) + ',' + py.toFixed(1) + ')' +
          (r.median_residual_px !== null && r.median_residual_px !== undefined
            ? '，回绘中位残差 ' + r.median_residual_px.toFixed(2) + 'px，状态 ' + r.verification_status : '');
        await selectCurve(state.curveId);
      } catch (err) { toast('添加点失败: ' + err.message); }
      return;
    }
    state.calDraft[state.pickMode] = { px: px, py: py };
    $('px_' + state.pickMode).textContent = '(' + px.toFixed(1) + ',' + py.toFixed(1) + ')';
    state.pickMode = null;
    $('pickHint').textContent = '';
  });
  $('btnCalib').addEventListener('click', async function () {
    var d = state.calDraft;
    if (!d.x1 || !d.x2 || !d.y1 || !d.y2) { toast('需要 X/Y 轴各 2 个拾取点'); return; }
    var vals = {};
    ['x1', 'x2', 'y1', 'y2'].forEach(function (k) { vals[k] = parseFloat($('v_' + k).value); });
    if ([vals.x1, vals.x2, vals.y1, vals.y2].some(isNaN)) { toast('请填写 4 个刻度值'); return; }
    var spec = { axes: {
      x: { scale: $('xScale').value, unit: $('xUnit').value.trim() || null,
           points: [{ px: d.x1.px, py: d.x1.py, value: vals.x1 },
                    { px: d.x2.px, py: d.x2.py, value: vals.x2 }] },
      y: { scale: $('yScale').value, unit: $('yUnit').value.trim() || null,
           points: [{ px: d.y1.px, py: d.y1.py, value: vals.y1 },
                    { px: d.y2.px, py: d.y2.py, value: vals.y2 }] } } };
    try {
      var r = await apiPost('/api/curve_calibration', { curve_id: state.curveId, spec: spec });
      $('curveMsg').textContent = '校准已保存' + (r.overlay
        ? '；回绘中位残差 ' + (r.overlay.median_residual_px === null ? 'n/a' : r.overlay.median_residual_px.toFixed(2) + 'px') +
          '，状态 ' + r.overlay.verification_status : '');
      await selectCurve(state.curveId);
      await loadCurves();
    } catch (e) { toast('校准失败: ' + e.message); }
  });
  $('btnWpd').addEventListener('click', async function () {
    var txt = $('wpdCsv').value.trim();
    if (!txt) { toast('请粘贴 WebPlotDigitizer 导出的 CSV'); return; }
    try {
      var r = await apiPost('/api/curve_wpd', { curve_id: state.curveId, csv_text: txt,
                                                series: $('wpdSeries').value.trim() || null });
      toast('WPD 导入完成: 系列 ' + r.series + '，' + r.n_points + ' 点，状态 ' + r.verification_status);
      await selectCurve(state.curveId);
      await loadCurves();
    } catch (e) { toast('WPD 导入失败: ' + e.message); }
  });
  [['btnCurveAccept', 'accept'], ['btnCurveReject', 'reject'], ['btnCurveUnsure', 'flag_uncertain']]
    .forEach(function (pair) {
      $(pair[0]).addEventListener('click', async function () {
        if (!state.curveId) return;
        var comment = prompt('复核备注（曲线 ' + pair[1] + '）：', '') || '';
        try {
          var r = await apiPost('/api/review', { target_type: 'curve', target_id: state.curveId,
            action: pair[1], comment: comment, reviewer: reviewer(), paper_id: state.paperId });
          toast('曲线审核已记录 #' + r.review_id);
          await selectCurve(state.curveId);
          await Promise.all([loadCurves(), loadReviews(), loadFlags()]);
        } catch (e) { toast('审核失败: ' + e.message); }
      });
    });
}

async function init() {
  initEvents();
  try { await loadPapers(); }
  catch (e) { toast('加载论文列表失败: ' + e.message); }
}
document.addEventListener('DOMContentLoaded', init);

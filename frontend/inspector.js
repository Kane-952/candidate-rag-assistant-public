const table = document.querySelector('#records');
const tbody = table.querySelector('tbody');
const notice = document.querySelector('#notice');
const refresh = document.querySelector('#refresh');
const filterForm = document.querySelector('#email-filter');
const filterEmail = document.querySelector('#filter-email');
const clearFilter = document.querySelector('#clear-filter');

function node(tag, className = '', value = '') {
  const item = document.createElement(tag);
  if (className) item.className = className;
  item.textContent = value == null ? '' : String(value);
  return item;
}
function addText(parent, tag, className, value) {
  const item = node(tag, className, value);
  parent.append(item);
  return item;
}
function valueOrDash(value) { return value == null || value === '' ? '—' : String(value); }
function ms(value) { return value == null ? '—' : `${Number(value).toFixed(1)} ms`; }
function feedback(value) { return ({up:'👍 点赞', down:'👎 点踩', none:'已取消'}[value] || '—'); }
function paragraph(section, label, value) {
  addText(section, 'p', 'label', label);
  addText(section, 'pre', '', valueOrDash(value));
}
function section(container, title) {
  const block = node('section');
  addText(block, 'h3', '', title);
  container.append(block);
  return block;
}
function metric(container, label, value) {
  const box = node('div', 'metric');
  addText(box, 'span', 'label', label);
  addText(box, 'strong', '', ms(value));
  container.append(box);
}
function formatHistory(history) {
  return history?.length ? history.map(item => `${item.role === 'user' ? '用户' : '助手'}：${item.content}`).join('\n\n') : '首次提问，无多轮历史';
}
function chunkTable(container, record) {
  const retrieved = record.retrieved || [];
  const reranked = record.reranked || [];
  const candidates = record.context_candidates || [];
  const finalIds = new Set((record.final_context || []).map(item => item.chunk_id));
  const gateIds = new Set(record.answerability?.evidence_ids || []);
  const rows = new Map();
  for (const [i, hit] of retrieved.entries()) rows.set(hit.chunk_id, {hit, retrievalRank:i + 1});
  for (const [i, hit] of reranked.entries()) {
    const row = rows.get(hit.chunk_id) || {hit};
    row.hit = hit; row.rerankRank = i + 1; rows.set(hit.chunk_id, row);
  }
  for (const hit of candidates) if (!rows.has(hit.chunk_id)) rows.set(hit.chunk_id, {hit, supplemented:true});
  if (!rows.size) { addText(container, 'p', 'muted', '无检索结果。'); return; }
  const wrap = node('div', 'table-wrap');
  const listing = node('table', 'chunk-table');
  const head = node('thead');
  const heading = node('tr');
  for (const label of ['检索', '重排', '来源 / 内容', '检索分数', '重排分数', '进入 Context', '选择路径']) addText(heading, 'th', '', label);
  head.append(heading); listing.append(head);
  const body = node('tbody');
  for (const row of rows.values()) {
    const hit = row.hit;
    const tr = node('tr');
    addText(tr, 'td', '', row.retrievalRank ? `#${row.retrievalRank}` : '补充');
    addText(tr, 'td', '', row.rerankRank ? `#${row.rerankRank}` : '—');
    const sourceCell = node('td', 'source-name');
    addText(sourceCell, 'div', '', hit.title || hit.source);
    addText(sourceCell, 'div', 'muted', hit.source);
    addText(sourceCell, 'code', '', hit.chunk_id);
    const detail = node('details');
    addText(detail, 'summary', '', '查看 chunk 内容');
    addText(detail, 'pre', '', hit.text);
    sourceCell.append(detail); tr.append(sourceCell);
    addText(tr, 'td', '', hit.score == null ? '—' : Number(hit.score).toFixed(4));
    addText(tr, 'td', '', hit.rerank_score == null ? '—' : Number(hit.rerank_score).toFixed(4));
    const included = finalIds.has(hit.chunk_id);
    const selected = node('td');
    addText(selected, 'span', included ? 'tag' : 'tag no', included ? '是' : '否');
    tr.append(selected);
    const path = included ? '证据判断选中' : gateIds.has(hit.chunk_id) ? '证据候选，未送入生成' : row.rerankRank ? '重排入选，证据未选' : row.supplemented ? '主题补充，未选中' : '检索命中，重排未入选';
    addText(tr, 'td', 'muted', path);
    body.append(tr);
  }
  listing.append(body); wrap.append(listing); container.append(wrap);
  addText(container, 'p', 'label', '检索分数为混合检索 RRF 分数；重排分数仅用于排序，均不是回答正确率。');
}
function renderDetail(container, record) {
  const identity = section(container, '提问身份与会话');
  paragraph(identity, '已验证的登录邮箱', record.user_email);
  paragraph(identity, '提问时间', new Date(record.timestamp).toLocaleString('zh-CN'));
  paragraph(identity, '会话 ID（区分同一邮箱的多轮对话）', record.conversation_id || '旧记录未关联会话 ID');

  const flow = section(container, '问答路径');
  paragraph(flow, '用户原始问题', record.question);
  paragraph(flow, '多轮上下文', formatHistory(record.history));
  paragraph(flow, 'Query Rewrite / 实际检索问题', record.rewritten_query || record.question);

  const retrieval = section(container, `检索与重排 · Top-K ${record.retrieved?.length || 0} → ${record.reranked?.length || 0}`);
  chunkTable(retrieval, record);

  const context = section(container, `最终送给 LLM 的 Context · ${record.final_context?.length || 0} 个片段`);
  if (!record.final_context?.length) addText(context, 'p', 'muted', '未进入回答生成阶段。');
  const contextList = node('div', 'context-list');
  for (const hit of record.final_context || []) {
    const detail = node('details', 'context-item');
    addText(detail, 'summary', '', `${hit.title} · ${hit.source}`);
    addText(detail, 'code', '', hit.chunk_id);
    addText(detail, 'pre', '', hit.text);
    contextList.append(detail);
  }
  context.append(contextList);

  const decision = section(container, '回答状态与依据');
  addText(decision, 'p', 'status ' + record.status.toLowerCase(), record.status);
  paragraph(decision, '主要依据', record.status_reason);
  if (record.answerability) paragraph(decision, '证据判断', `${record.answerability.reason}（confidence: ${record.answerability.confidence}）`);
  if (record.verification) paragraph(decision, '回答核验', `${record.verification.reason}（confidence: ${record.verification.confidence}）`);
  if (record.error) paragraph(decision, '请求错误', record.error);

  const generation = section(container, 'DeepSeek Flash 生成与最终回答');
  if (record.generation) paragraph(generation, '模型生成内容（校验前）', record.generation.answer);
  paragraph(generation, '最终展示给用户', record.answer);
  paragraph(generation, '引用来源', record.sources?.length ? record.sources.map(item => `${item.title} · ${item.source} · ${item.chunk_id}`).join('\n') : '无');
  paragraph(generation, '点赞 / 点踩', feedback(record.feedback));
  if (record.feedback === 'down') addText(generation, 'p', 'label', '排查点踩：先看正确依据是否出现在检索结果；若出现但未进入 Context，检查重排与证据选择；若已进入 Context，再检查生成内容与最终核验。');

  const timing = section(container, '耗时监控');
  const metrics = node('div', 'metrics');
  const d = record.timing?.durations_ms || {};
  const e = record.timing?.events_ms || {};
  for (const [label, val] of [
    ['鉴权 / Session', d.auth_session], ['请求排队', d.lock_wait],
    ['知识库加载', d.knowledge_base_load], ['索引加载', d.index_load],
    ['Embedding 模型加载', d.embedding_model_load], ['Reranker 模型加载', d.reranker_model_load],
    ['Query Rewrite API', d.llm_rewrite_api], ['Query Embedding', d.query_embedding],
    ['Retrieval', d.retrieval_total], ['Rerank', d.reranker_total],
    ['证据判断', d.answerability_total], ['Prompt 构建（累计）', d.prompt_build],
    ['首 token TTFT', d.ttft], ['完整生成', d.generation_total],
    ['最终核验', d.verification_total], ['模型 / 索引初始化', d.pipeline_init],
    ['总耗时', record.timing?.total_ms]
  ]) if (val != null || label === '首 token TTFT') metric(metrics, label, val);
  timing.append(metrics);
  if (e.answer_api_start_ms != null) paragraph(timing, '生成 API 请求开始（相对请求开始）', ms(e.answer_api_start_ms));
  if (e.first_stream_token_ms != null) paragraph(timing, '第一个流式 token（相对请求开始）', ms(e.first_stream_token_ms));
  addText(timing, 'p', 'label', 'TTFT 为生成 API 请求至首 token 的时间；完整生成包含模型输出与解析。同步接口不产生流式 TTFT。');
}
async function loadDetail(row, button, id) {
  if (row.hidden === false) { row.hidden = true; button.textContent = '查看详情'; return; }
  if (row.dataset.loaded) { row.hidden = false; button.textContent = '收起详情'; return; }
  button.disabled = true; button.textContent = '加载中…';
  try {
    const response = await fetch(`/inspector/records/${id}`);
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('详情加载失败，请重试。');
    renderDetail(row.querySelector('.detail'), await response.json());
    row.dataset.loaded = 'true'; row.hidden = false; button.textContent = '收起详情';
  } catch (error) { button.textContent = '查看详情'; notice.textContent = error.message; }
  finally { button.disabled = false; }
}
async function loadRecords() {
  refresh.disabled = true;
  notice.textContent = '正在加载…';
  try {
    const email = filterEmail.value.trim().toLowerCase();
    const url = email ? `/inspector/records?${new URLSearchParams({email})}` : '/inspector/records';
    const response = await fetch(url);
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('记录加载失败，请重试。');
    const {records} = await response.json();
    tbody.replaceChildren();
    for (const record of records) {
      const tr = node('tr');
      const emailCell = node('td');
      const emailButton = addText(emailCell, 'button', 'email-button', record.user_email);
      emailButton.type = 'button';
      emailButton.setAttribute('aria-label', `筛选 ${record.user_email} 的问答`);
      emailButton.addEventListener('click', () => { filterEmail.value = record.user_email; loadRecords(); });
      tr.append(emailCell);
      addText(tr, 'td', 'muted', new Date(record.timestamp).toLocaleString('zh-CN'));
      addText(tr, 'td', 'question', record.question);
      const statusCell = node('td');
      addText(statusCell, 'span', 'status ' + record.status.toLowerCase(), record.status);
      tr.append(statusCell);
      addText(tr, 'td', '', ms(record.total_ms));
      const action = node('td');
      const button = addText(action, 'button', 'detail-button', '查看详情');
      button.type = 'button'; tr.append(action);
      const detailRow = node('tr', 'detail-row');
      detailRow.hidden = true;
      const cell = node('td'); cell.colSpan = 6;
      cell.append(node('div', 'detail')); detailRow.append(cell);
      button.addEventListener('click', () => loadDetail(detailRow, button, record.request_id));
      tbody.append(tr, detailRow);
    }
    table.hidden = records.length === 0;
    notice.textContent = records.length ? '' : email ? '该邮箱在保留范围内暂无问答记录。' : '暂无调试记录。新问答完成后会出现在这里。';
  } catch (error) { notice.textContent = error.message; }
  finally { refresh.disabled = false; }
}
refresh.addEventListener('click', loadRecords);
filterForm.addEventListener('submit', event => { event.preventDefault(); loadRecords(); });
clearFilter.addEventListener('click', () => { filterEmail.value = ''; loadRecords(); });
loadRecords();

const accessTable = document.querySelector('#access-records');
const accessBody = accessTable.querySelector('tbody');
const accessNotice = document.querySelector('#access-notice');
const accessFilter = document.querySelector('#access-filter');
const accessEmail = document.querySelector('#access-email');
const clearAccessFilter = document.querySelector('#clear-access-filter');
const refreshAccess = document.querySelector('#refresh-access');

async function loadAccessLog() {
  refreshAccess.disabled = true;
  accessNotice.textContent = '正在加载…';
  try {
    const email = accessEmail.value.trim().toLowerCase();
    const url = email ? `/inspector/access-log?${new URLSearchParams({email})}` : '/inspector/access-log';
    const response = await fetch(url);
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('登录日志加载失败，请重试。');
    const {records} = await response.json();
    accessBody.replaceChildren();
    for (const record of records) {
      const row = node('tr');
      addText(row, 'td', '', record.user_email);
      addText(row, 'td', '', valueOrDash(record.ip_address));
      addText(row, 'td', 'muted', new Date(record.login_time * 1000).toLocaleString('zh-CN'));
      addText(row, 'td', 'muted', record.session_id);
      accessBody.append(row);
    }
    accessTable.hidden = records.length === 0;
    accessNotice.textContent = records.length ? '' : '暂无符合条件的成功登录记录。';
  } catch (error) { accessNotice.textContent = error.message; }
  finally { refreshAccess.disabled = false; }
}

accessFilter.addEventListener('submit', event => { event.preventDefault(); loadAccessLog(); });
clearAccessFilter.addEventListener('click', () => { accessEmail.value = ''; loadAccessLog(); });
refreshAccess.addEventListener('click', loadAccessLog);
loadAccessLog();
const feedbackTable = document.querySelector('#feedback-records');
const feedbackBody = feedbackTable.querySelector('tbody');
const feedbackNotice = document.querySelector('#feedback-notice');
const feedbackFilter = document.querySelector('#feedback-filter');
const feedbackEmail = document.querySelector('#feedback-email');
const feedbackReasonFilter = document.querySelector('#feedback-reason-filter');
const feedbackStatusFilter = document.querySelector('#feedback-status-filter');
const clearFeedbackFilter = document.querySelector('#clear-feedback-filter');

async function postInspector(url, payload) {
  const response = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (response.status === 401) { location.assign('/login'); throw new Error('请重新登录。'); }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '保存失败，请重试。');
  return result;
}

function labeledSelect(form, labelText, choices, selected, required = true) {
  const label = node('label');
  addText(label, 'span', '', labelText);
  const select = node('select');
  select.required = required;
  for (const value of choices) {
    const option = node('option', '', value || '请选择');
    option.value = value;
    select.append(option);
  }
  select.value = selected || '';
  label.append(select); form.append(label);
  return select;
}

function labeledInput(form, labelText, value, maxLength = 200) {
  const label = node('label');
  addText(label, 'span', '', labelText);
  const input = node('input');
  input.type = 'text'; input.maxLength = maxLength; input.value = value || '';
  label.append(input); form.append(label);
  return input;
}

function renderFeedbackDetail(container, record, summaryRow) {
  renderDetail(container, record);
  const item = record.feedback_case;
  const triage = section(container, '点踩原因与人工归因');
  paragraph(triage, '用户体验原因', item.user_feedback_reason || '旧记录未收集体验原因');
  if (item.user_comment) paragraph(triage, '用户备注', item.user_comment);
  if (!item.user_feedback_reason && item.feedback_reason)
    paragraph(triage, '旧版点踩选项（仅供参考）', item.feedback_reason);
  paragraph(triage, 'answer_id / trace_id', `${item.answer_id} / ${item.request_id || '旧记录无 Trace ID'}`);
  const message = addText(triage, 'p', 'triage-status', '');

  const analysis = node('form', 'analysis-form');
  const rootCause = labeledSelect(analysis, '管理员技术归因', [
    'Retrieval 问题', 'Generation / Prompt 问题', 'Refusal 问题',
    'Citation 问题', '回答风格问题', '知识库资料不足', '其他', '待人工判断'
  ], item.admin_root_cause);
  const workflow = labeledSelect(analysis, '处理状态',
    ['待处理', '已分析', '已修复', '已加入回归测试'], item.workflow_status);
  const save = addText(analysis, 'button', '', '保存分析');
  save.type = 'submit';
  analysis.addEventListener('submit', async event => {
    event.preventDefault(); save.disabled = true; message.textContent = '保存中…';
    try {
      await postInspector(`/inspector/feedback/${item.answer_id}/analysis`, {
        admin_root_cause: rootCause.value, workflow_status: workflow.value
      });
      item.admin_root_cause = rootCause.value; item.workflow_status = workflow.value;
      summaryRow.querySelector('.case-attribution').textContent = item.admin_root_cause;
      summaryRow.querySelector('.case-workflow').textContent = item.workflow_status;
      message.textContent = '分析已保存。';
    } catch (error) { message.textContent = error.message; }
    finally { save.disabled = false; }
  });
  triage.append(analysis);

  const regression = section(container, '加入回归测试集');
  addText(regression, 'p', 'label', '先将处理状态设为“已修复”。测试会核对回答状态，以及选填的来源和关键词；期望行为备注供人工复核。');
  const form = node('form', 'regression-form');
  const expected = labeledSelect(form, '期望回答状态', ['', 'ANSWER', 'PARTIAL', 'REFUSE'],
    item.regression?.expected_status || '');
  const source = labeledInput(form, '期望引用来源（可选）', item.regression?.expected_source, 300);
  const phrase = labeledInput(form, '回答应包含的关键词（可选）', item.regression?.expected_text, 200);
  const behaviorLabel = node('label');
  addText(behaviorLabel, 'span', '', '期望行为说明（可选）');
  const behavior = node('textarea');
  behavior.maxLength = 500; behavior.value = item.regression?.expected_behavior || '';
  behaviorLabel.append(behavior); form.append(behaviorLabel);
  const add = addText(form, 'button', '', item.regression ? '更新回归案例' : '加入回归测试');
  add.type = 'submit';
  const regressionMessage = addText(regression, 'p', 'triage-status', '');
  form.addEventListener('submit', async event => {
    event.preventDefault(); add.disabled = true; regressionMessage.textContent = '保存中…';
    try {
      await postInspector(`/inspector/feedback/${item.answer_id}/regression`, {
        expected_status: expected.value, expected_source: source.value,
        expected_text: phrase.value, expected_behavior: behavior.value
      });
      item.workflow_status = '已加入回归测试';
      workflow.value = item.workflow_status;
      summaryRow.querySelector('.case-workflow').textContent = item.workflow_status;
      add.textContent = '更新回归案例';
      regressionMessage.textContent = '已加入本地回归测试集。';
    } catch (error) { regressionMessage.textContent = error.message; }
    finally { add.disabled = false; }
  });
  regression.append(form);
}

async function loadFeedbackDetail(row, button, id, summaryRow) {
  if (row.hidden === false) { row.hidden = true; button.textContent = '查看详情'; return; }
  if (row.dataset.loaded) { row.hidden = false; button.textContent = '收起详情'; return; }
  button.disabled = true; button.textContent = '加载中…';
  try {
    const response = await fetch(`/inspector/feedback/${id}`);
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('反馈详情加载失败，请重试。');
    renderFeedbackDetail(row.querySelector('.detail'), await response.json(), summaryRow);
    row.dataset.loaded = 'true'; row.hidden = false; button.textContent = '收起详情';
  } catch (error) { button.textContent = '查看详情'; feedbackNotice.textContent = error.message; }
  finally { button.disabled = false; }
}

async function loadFeedback() {
  feedbackNotice.textContent = '正在加载…';
  const params = new URLSearchParams();
  if (feedbackEmail.value.trim()) params.set('email', feedbackEmail.value.trim().toLowerCase());
  if (feedbackReasonFilter.value) params.set('reason', feedbackReasonFilter.value);
  if (feedbackStatusFilter.value) params.set('answer_status', feedbackStatusFilter.value);
  try {
    const response = await fetch(`/inspector/feedback${params.size ? `?${params}` : ''}`);
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('反馈记录加载失败，请重试。');
    const {records} = await response.json();
    feedbackBody.replaceChildren();
    for (const item of records) {
      const tr = node('tr');
      addText(tr, 'td', 'muted', new Date(item.timestamp).toLocaleString('zh-CN'));
      addText(tr, 'td', '', item.user_email);
      addText(tr, 'td', 'question', item.question);
      addText(tr, 'td', '', item.user_feedback_reason || '旧记录未收集');
      const statusCell = node('td');
      addText(statusCell, 'span', 'status ' + item.answer_status.toLowerCase(), item.answer_status);
      tr.append(statusCell);
      addText(tr, 'td', 'case-attribution', item.admin_root_cause);
      addText(tr, 'td', 'case-workflow', item.workflow_status);
      const action = node('td');
      const button = addText(action, 'button', 'detail-button', '查看详情');
      button.type = 'button'; tr.append(action);
      const detailRow = node('tr', 'detail-row');
      detailRow.hidden = true;
      const cell = node('td'); cell.colSpan = 8;
      cell.append(node('div', 'detail')); detailRow.append(cell);
      button.addEventListener('click', () => loadFeedbackDetail(detailRow, button, item.answer_id, tr));
      feedbackBody.append(tr, detailRow);
    }
    feedbackTable.hidden = records.length === 0;
    feedbackNotice.textContent = records.length ? '' : '当前条件下没有点踩记录。';
  } catch (error) { feedbackNotice.textContent = error.message; }
}

feedbackFilter.addEventListener('submit', event => { event.preventDefault(); loadFeedback(); });
clearFeedbackFilter.addEventListener('click', () => {
  feedbackEmail.value = ''; feedbackReasonFilter.value = ''; feedbackStatusFilter.value = '';
  loadFeedback();
});
loadFeedback();

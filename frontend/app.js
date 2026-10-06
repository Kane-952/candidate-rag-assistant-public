const app = document.querySelector('.app');
const messages = document.querySelector('#messages');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const reset = document.querySelector('#reset');
const logout = document.querySelector('#logout');
const form = document.querySelector('#chat-form');
const hint = document.querySelector('#input-hint');
function renderSuggestions() {
  const grid = messages.querySelector('.suggestion-grid');
  for (const question of window.RECOMMENDED_QUESTIONS) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'suggestion-chip';
    chip.textContent = question;
    grid.append(chip);
  }
}
renderSuggestions();
const welcome = messages.querySelector('.welcome').cloneNode(true);
let sessionId = crypto.randomUUID();
let busy = false;

function nearBottom() {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 100;
}
function syncInput() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  send.disabled = busy || !input.value.trim();
}
function scrollToLatest() {
  messages.scrollTop = messages.scrollHeight;
}
function addMessage(role, text, extraClass = '') {
  messages.querySelector('.welcome')?.remove();
  app.classList.remove('is-empty');
  const article = document.createElement('article');
  article.className = 'message ' + role + ' ' + extraClass;
  article.setAttribute('aria-label', role === 'user' ? '你的消息' : '示例候选人 AI Assistant 的回复');
  if (role === 'assistant') {
    const label = document.createElement('div');
    label.className = 'message-label';
    const avatar = document.createElement('span');
    avatar.className = 'message-avatar';
    const image = document.createElement('img');
    image.src = '/assets/demo-avatar.svg';
    image.alt = '';
    image.width = 35;
    image.height = 35;
    avatar.append(image);
    const identity = document.createElement('span');
    identity.className = 'identity';
    const name = document.createElement('strong');
    name.textContent = '示例候选人 Demo';
    const roleName = document.createElement('span');
    roleName.textContent = 'AI Assistant';
    identity.append(name, roleName);
    label.append(avatar, identity);
    article.append(label);
  }
  const content = document.createElement('div');
  content.className = 'content';
  content.textContent = text;
  article.append(content);
  messages.append(article);
  return article;
}
function showAnswer(article, answer) {
  const content = article.querySelector('.content');
  // The API keeps citation IDs and Sources for feedback and Inspector; only the chat text is simplified.
  content.textContent = answer.replace(/[ \t]*\[[0-9a-f]{20}\]/g, '');
}
function pendingMessage() {
    const article = addMessage('assistant', '', 'pending');
    const content = article.querySelector('.content');
    const label = document.createElement('span');
    label.textContent = '正在思考...';
  const dots = document.createElement('span');
  dots.className = 'dots';
  dots.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 3; i++) dots.append(document.createElement('i'));
  content.append(label, dots);
  return article;
}
async function ask(question) {
  question = question.trim();
  if (busy || !question) return;
  busy = true;
  reset.disabled = true;
  addMessage('user', question);
  input.value = '';
  hint.textContent = '可以先写下你的下一个问题。';
  syncInput();
  const pending = pendingMessage();
  scrollToLatest();
  try {
    const response = await fetch('/chat/stream', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:sessionId, message:question})
    });
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok || !response.body) {
      const errorBody = await response.json().catch(() => ({}));
      throw new Error(errorBody.detail || '这次没能收到回复，请再试一次。');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = false;
    const content = pending.querySelector('.content');
    const handle = event => {
      if (event.type === 'delta') {
        if (pending.classList.contains('pending')) {
          pending.classList.remove('pending');
          content.textContent = '';
        }
        const follow = nearBottom();
        content.textContent += event.text;
        if (follow) scrollToLatest();
      } else if (event.type === 'done') {
        const follow = nearBottom();
        pending.classList.remove('pending');
        content.textContent = '';
        showAnswer(pending, event.answer);
        if (event.answer_id) addFeedback(pending, event.answer_id, sessionId);
        if (follow) scrollToLatest();
        completed = true;
      } else if (event.type === 'error') {
        throw new Error(event.message || '这次没能完成回复，请再试一次。');
      }
    };
    while (!completed) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream:true});
      let lineEnd;
      while ((lineEnd = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, lineEnd).trim();
        buffer = buffer.slice(lineEnd + 1);
        if (line) handle(JSON.parse(line));
      }
    }
    if (!completed) throw new Error('连接中断，回复未完成。');
  } catch (error) {
    pending.classList.remove('pending');
    const content = pending.querySelector('.content');
    content.textContent = '';
    const notice = document.createElement('div');
    notice.className = 'stream-error';
    notice.textContent = error.message || '这次没能完成回复，请再试一次。';
    pending.append(notice);
    // 用户可能已在等待时写下下一问，不能覆盖其草稿。
    if (!input.value.trim()) input.value = question;
    if (nearBottom()) scrollToLatest();
  } finally {
    busy = false;
    reset.disabled = false;
    hint.textContent = '随时提问，也可以接着聊。';
    syncInput();
    if (document.activeElement === send || document.activeElement === document.body) input.focus({preventScroll:true});
  }
}
form.addEventListener('submit', event => { event.preventDefault(); ask(input.value); });
messages.addEventListener('click', event => {
  const chip = event.target.closest('.suggestion-chip');
  if (chip && messages.contains(chip)) ask(chip.textContent);
});
input.addEventListener('input', syncInput);
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  }
});
reset.addEventListener('click', () => {
  if (busy) return;
  sessionId = crypto.randomUUID();
  messages.replaceChildren(welcome.cloneNode(true));
  app.classList.add('is-empty');
  reset.disabled = true;
  input.value = '';
  syncInput();
  input.focus({preventScroll:true});
});
syncInput();

logout.addEventListener('click', async () => {
  logout.disabled = true;
  try {
    await fetch('/auth/logout', {method:'POST'});
    location.assign('/login');
  } catch {
    logout.disabled = false;
    hint.textContent = '退出失败，请重试。';
  }
});


function chooseDislikeReason() {
  return new Promise(resolve => {
    let choice = null;
    const dialog = document.createElement('dialog');
    dialog.className = 'feedback-dialog';
    dialog.setAttribute('aria-labelledby', 'feedback-dialog-title');
    const form = document.createElement('form');
    const title = document.createElement('h2');
    title.id = 'feedback-dialog-title';
    title.textContent = '哪里需要改进？';
    const description = document.createElement('p');
    description.textContent = '说说这次回答的使用体验即可。';
    const label = document.createElement('label');
    label.htmlFor = 'feedback-reason';
    label.textContent = '哪里没有达到预期？';
    const select = document.createElement('select');
    select.id = 'feedback-reason';
    select.required = true;
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '请选择';
    select.append(placeholder);
    for (const reason of [
      '没有回答我的问题', '回答不够清楚', '回答太长',
      '回答太简略', '没有覆盖我关心的重点', '其他'
    ]) {
      const option = document.createElement('option');
      option.value = reason;
      option.textContent = reason;
      select.append(option);
    }
    const noteGroup = document.createElement('div');
    noteGroup.hidden = true;
    const noteLabel = document.createElement('label');
    noteLabel.htmlFor = 'feedback-note';
    noteLabel.textContent = '补充备注（可选）';
    const note = document.createElement('textarea');
    note.id = 'feedback-note';
    note.maxLength = 300;
    note.rows = 3;
    note.placeholder = '简要说明哪里不对';
    noteGroup.append(noteLabel, note);
    select.addEventListener('change', () => { noteGroup.hidden = select.value !== '其他'; });
    const actions = document.createElement('div');
    actions.className = 'feedback-dialog-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = '取消';
    cancel.addEventListener('click', () => dialog.close());
    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.textContent = '提交点踩';
    actions.append(cancel, submit);
    form.append(title, description, label, select, noteGroup, actions);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!select.value) return;
      choice = {user_feedback_reason: select.value, user_comment: select.value === '其他' ? note.value.trim() : null};
      dialog.close();
    });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(choice); }, {once: true});
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    select.focus();
  });
}

function addFeedback(article, answerId, answerSessionId) {
  let rating = 'none';
  const bar = document.createElement('div');
  bar.className = 'feedback';
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', '评价这条回复');
  const status = document.createElement('span');
  status.setAttribute('role', 'status');
  const buttons = ['up', 'down'].map(value => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = value === 'up' ? '赞' : '踩';
    button.setAttribute('aria-label', value === 'up' ? '点赞这条回复' : '点踩这条回复');
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', async () => {
      const next = rating === value ? 'none' : value;
      const reason = next === 'down' ? await chooseDislikeReason() : null;
      if (next === 'down' && !reason) return;
      buttons.forEach(item => item.disabled = true);
      status.textContent = '保存中…';
      try {
        const response = await fetch('/feedback', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            answer_id: answerId, session_id: answerSessionId,
            rating: next === 'up' ? 'like' : next === 'down' ? 'dislike' : 'none',
            ...(reason || {})
          })
        });
        if (response.status === 401) { location.assign('/login'); return; }
        if (!response.ok) throw new Error('Save failed');
        rating = next;
        buttons.forEach((item, i) => item.setAttribute('aria-pressed', String(rating === ['up', 'down'][i])));
        status.textContent = rating === 'none' ? '已取消评价' : '感谢反馈，已记录';
      } catch {
        status.textContent = '未保存，请重试';
      } finally {
        buttons.forEach(item => item.disabled = false);
      }
    });
    bar.append(button);
    return button;
  });
  bar.append(status);
  article.append(bar);
}

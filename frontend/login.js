const form = document.querySelector('#login-form');
const email = document.querySelector('#email');
const submit = document.querySelector('#submit');
const status = document.querySelector('#status');
const token = new URLSearchParams(location.hash.slice(1)).get('token');

async function verify(token) {
  history.replaceState(null, '', '/login');
  status.textContent = '正在验证登录链接…';
  form.hidden = true;
  try {
    const response = await fetch('/auth/verify', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token})
    });
    if (!response.ok) throw new Error('链接无效或已过期，请重新申请。');
    location.replace('/');
  } catch (error) {
    form.hidden = false;
    status.textContent = error.message || '验证失败，请重新申请。';
  }
}

if (token) verify(token);

form.addEventListener('submit', async event => {
  event.preventDefault();
  submit.disabled = true;
  status.textContent = '正在发送…';
  try {
    const response = await fetch('/auth/request', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:email.value})
    });
    if (!response.ok) throw new Error('暂时无法发送，请稍后重试。');
    const result = await response.json();
    status.textContent = result.message;
  } catch (error) {
    status.textContent = error.message || '暂时无法发送，请稍后重试。';
  } finally {
    submit.disabled = false;
  }
});

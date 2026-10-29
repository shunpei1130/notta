'use strict';
window.nottaWait = (() => {
  const dialog = document.querySelector('#wait-dialog');
  const title = document.querySelector('#wait-title');
  const detail = document.querySelector('#wait-detail');
  let active = null;
  const timeoutError = () => Object.assign(new Error('処理が完了したか確認できませんでした。再送せず、状態を確認してください。'), {waitTimeout:true});
  dialog.addEventListener('cancel', event => event.preventDefault());
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Tab') { event.preventDefault(); dialog.focus(); }
  });
  function message(value) { title.textContent = value; }
  async function run(label, task) {
    if (active) { message(label); return task(); }
    document.querySelector('#wait-recovery')?.remove();
    const op = {controller:new AbortController(), expired:false, navigating:false};
    active = op;
    message(label); detail.textContent = '画面はそのままでお待ちください。';
    if (!dialog.open) dialog.showModal();
    document.querySelector('#boot-wait')?.remove();
    document.documentElement.classList.add('is-waiting');
    document.querySelector('#main').setAttribute('aria-busy', 'true');
    function finish() {
      clearTimeout(slow); clearTimeout(deadline);
      if (active !== op) return;
      active = null; dialog.close();
      document.documentElement.classList.remove('is-waiting');
      document.querySelector('#main').removeAttribute('aria-busy');
      const focus = document.querySelector('#current-question') || document.querySelector('#main h1');
      if (focus) { focus.tabIndex = -1; focus.focus({preventScroll:true}); }
    }
    const slow = setTimeout(() => { if (active === op) detail.textContent = '通常より時間がかかっています。このままお待ちください。'; }, 10000);
    let deadline;
    const timeout = new Promise((_, reject) => { deadline = setTimeout(() => {
      op.expired = true; op.controller.abort();
      if (op.navigating) {
        finish(); recover(timeoutError(), () => { location.href = op.destination || location.href; });
      }
      reject(timeoutError());
    }, 60000); });
    try { return await Promise.race([Promise.resolve().then(task), timeout]); }
    finally {
      if (!op.navigating || op.expired) finish();
    }
  }
  function recover(error, action) {
    document.querySelector('#wait-recovery')?.remove();
    const box = document.createElement('section'); box.id = 'wait-recovery';
    box.className = 'banner warning wait-recovery'; box.setAttribute('role', 'alert'); box.tabIndex = -1;
    const text = document.createElement('p'); text.textContent = error.message; box.append(text);
    const button = document.createElement('button'); button.className = 'button secondary compact';
    button.textContent = 'ログイン・回答の保存状況を確認する';
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      try { await run('保存した内容を確認しています', action); box.remove(); }
      catch (failure) { recover(failure, action); }
      finally { button.disabled = false; }
    });
    box.append(button); document.querySelector('#main').before(box); box.focus();
  }
  return {run, message, recover, get current() { return active; },
    check(op) { if (op?.expired) throw timeoutError(); },
    navigating(destination) { if (active) { active.navigating = true; active.destination = destination; } }};
})();

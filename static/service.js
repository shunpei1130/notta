'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function linkedText(value) {
  return String(value ?? '').split(/(https:\/\/[^\s<>"']+)/g).map(part => {
    if (!part.startsWith('https://')) return esc(part);
    try { const url = new URL(part); if (url.username || url.password) return esc(part); }
    catch { return esc(part); }
    return `<a href="${esc(part)}" target="_blank" rel="noopener noreferrer">${esc(part)} ↗</a>`;
  }).join('');
}
const state = {config: {}, me: null, project: null, dirty: false, busy: false, tab: 'draft', public: null};
let noticeTimer;
function notice(message, error = false) {
  const box = $('#notice'); box.textContent = message; box.classList.toggle('error', error); box.hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { box.hidden = true; }, error ? 12000 : 6000);
}
async function api(path, data) {
  let response;
  try { response = await fetch('/api/service' + path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    headers: data === undefined ? {} : {'Content-Type':'application/json','X-CSRF-Token':state.me?.csrf || ''}, body: data === undefined ? undefined : JSON.stringify(data)}); }
  catch { throw new Error('接続結果を確認できませんでした。再送前に保存内容や支払い状態を確認してください。'); }
  let result;
  try { result = await response.json(); } catch { throw new Error('接続結果を確認できませんでした。再送前に保存内容や契約状態を確認してください。'); }
  if (!response.ok) { const error = new Error(result.error || '処理できませんでした。'); error.status = response.status; throw error; }
  return result;
}
async function busy(button, task, target) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  if (target) target.textContent = '';
  try { return await task(); } catch (error) {
    if (target) target.textContent = error.message; else notice(error.message, true);
  } finally { if (button?.isConnected) button.disabled = false; }
}
const date = value => value ? new Date(value * 1000).toLocaleDateString('ja-JP', {timeZone:'Asia/Tokyo',year:'numeric',month:'long',day:'numeric'}) : '確認中';
function intent(value) { try { if (value === undefined) return sessionStorage.getItem('notta-intent') || ''; sessionStorage.setItem('notta-intent', value); } catch {} return ''; }
function clearIntent() { try { sessionStorage.removeItem('notta-intent'); } catch {} }
async function refreshMe() {
  try { state.me = await api('/me'); } catch (error) { if (error.status !== 401) throw error; state.me = null; }
  $('#account-button').textContent = state.me ? 'マイページ' : 'ログイン';
}
function headingFocus() { const title = $('#main h1'); if (title) { title.tabIndex = -1; title.focus({preventScroll:true}); } }
function main(html) { $('#main').innerHTML = html; }
function sandboxNotice() {
  if (!state.config.publicSandbox || $('#sandbox-notice')) return;
  const banner = document.createElement('aside');
  banner.id = 'sandbox-notice'; banner.className = 'sandbox-notice';
  banner.setAttribute('aria-label', '検証用サイト');
  banner.textContent = '検証用サイトです。決済はテストのみで、実際の請求はありません。';
  document.body.prepend(banner);
}
function navigate(path) { state.dirty = false; location.href = path; }

function landing() {
  document.title = 'ノッタ｜つくりたいを、みんなで。';
  main(`<section class="hero"><div class="hero-copy"><p class="eyebrow">NOTTA / IDEAS INTO TOGETHER</p>
    <h1>つくりたい。を、<br>みんなの<em>企画</em>に。</h1>
    <p>思いついた企画を、ひとりで抱えなくていい。<br>AIと話して、目的・役割・募集文を整理。<br>ファンと一歩を踏み出す準備を、ここから。</p>
    <form id="goal-form" class="goal-form"><label for="goal">どんな企画を、誰とつくりたいですか？</label>
      <textarea id="goal" name="goal" maxlength="1000" required placeholder="例：料理の発信を見てくれる人と、季節のレシピをまとめた小冊子をつくりたい">${esc(intent())}</textarea>
      <button class="button" type="submit">自分の企画を無料でつくる <span aria-hidden="true">↗</span></button>
      <p class="hint">メール認証で開始。カード登録不要・自動で課金されません。</p><p class="form-error" role="alert"></p></form>
    ${!state.config.aiReady || !state.config.mailReady ? '<p class="status-line">無料体験の接続を準備しています。入力した企画はこの画面に残せます。</p>' : ''}</div>
    <div class="example-board" aria-label="作成する企画のサンプル"><div class="board-caption"><strong>ひとつの思いつきから</strong><span class="sample-label">説明用のサンプル</span></div>
      <div class="paper"><p class="eyebrow">PROJECT NOTE 01</p><h3>季節のレシピを、<br>みんなの一冊に。</h3>
      <div class="paper-row"><strong>目指すこと</strong><p>いつもの料理に、小さな発見を届ける。</p></div>
      <div class="paper-row"><strong>最初の成果</strong><p>3つのレシピと試作メモをまとめた、6ページの冊子。</p></div>
      <div class="paper-row"><strong>一緒にする人</strong><p>試作して気づきをくれる人、読みやすさを確かめる人。</p></div>
      <div class="role-tags"><span class="role-tag">試作する人</span><span class="role-tag">読んで伝える人</span><span class="role-tag">まとめる人</span></div></div>
      <div class="board-foot"><span>企画概要 → 役割 → 募集文</span><span class="seal">一緒につくる</span></div></div></section>
    <div class="intro-strip"><span><b>01</b>自分の企画で、無料体験</span><span><b>02</b>原稿を保存・編集・コピー</span><span><b>03</b>募集から共同作業まで、ひとつに</span></div>
    <section class="section" id="how"><div class="section-head"><h2>思いつきを、動かせる企画へ。</h2><p>企画をつくるところまでは無料。人を募集して、一緒につくる段階で月額プランへ。</p></div>
      <div class="steps"><article class="step"><p class="step-num">01</p><h3>AIと、企画の芯をつくる</h3><p>やりたいことを入力。達成条件、最初の成果物、必要な役割を対話しながら整理します。</p></article>
      <article class="step"><p class="step-num">02</p><h3>自分の言葉に、整える</h3><p>企画概要と募集文を読み、自分で編集。保存した原稿は、あとから何度でも確認できます。</p></article>
      <article class="step"><p class="step-num">03</p><h3>仲間を迎えて、進める</h3><p>月額プランで募集ページを公開。応募者の比較、役割の分担、提出と確認まで進められます。</p></article></div></section>
    <section class="pricing" id="pricing"><div class="section"><div class="section-head"><h2>準備は無料。<br>一緒につくるなら、月額で。</h2><p>企画をつくっただけで請求されることはありません。ファン・協力者の参加は無料です。</p></div>
      <div class="price-grid"><article class="price-card"><p class="plan-label">まずは、自分の企画で</p><h3 class="price">0<span>円</span></h3><p>企画と募集の原稿をつくる</p><ul><li>企画概要・達成条件・最初の成果物</li><li>役割2〜3種類と募集文</li><li>AIとの対話は10回、検索は1回まで</li><li>1人1企画。保存・手編集・コピー</li></ul><a href="#goal" class="button secondary">無料で企画をつくる</a><p class="hint">カード登録不要／初期の受付枠は30人まで</p></article>
      <article class="price-card paid"><p class="plan-label">ファンと、一緒に進める</p><h3 class="price">3,000<span>円／月・税込総額</span></h3><p>募集公開から、成果の確認まで</p><ul><li>同時に1企画。完了後は次の企画へ</li><li>募集ページ公開・応募者の比較と担当決定</li><li>テキスト・URLで提出、確認と貢献記録</li><li>AIの対話100回・検索10回／契約月まで</li></ul><button class="button" data-action="workspace">まずは無料で企画をつくる</button><p class="hint">毎月自動更新／更新停止はマイページから<br>初回支払いから7日以内の申出で全額返金</p></article></div>
      <p class="hint">人が企画を制作・運営する費用は含みません。集客、人数、成果は保証しません。AI・検索は利用上限内で提供し、受付枠は全体の利用状況に応じて調整します。</p></div></section>
    <section class="section"><div class="section-head"><h2>はじめる前に。</h2></div>
      <details class="faq"><summary>無料でどこまで試せますか？</summary><p>ご自身の企画を入力してAIと相談し、企画概要、達成条件、最初の成果物、役割、募集文を作成できます。保存・手編集・コピーも無料です。募集の公開と共同作業から月額プランが必要です。</p></details>
      <details class="faq"><summary>企画に期限はありますか？</summary><p>14日で企画が強制終了することはありません。月額プランでは同時に1企画を進め、完了後に次の企画をつくれます。各企画の日程はご自身で決められます。</p></details>
      <details class="faq"><summary>解約したら、企画はどうなりますか？</summary><p>次回の自動更新を停止し、支払済み期間の終了まで有料機能を利用できます。その後も企画と記録を確認できます。募集公開と共同作業は停止します。</p></details>
      <details class="faq"><summary>AIが勝手に公開・連絡することはありますか？</summary><p>企画の公開、応募、担当決定、成果の確認は利用者が操作します。AIの案には不正確な情報が含まれるため、内容や権利、条件をご自身で確認してください。</p></details></section>`);
  $('#goal-form').addEventListener('submit', event => {
    event.preventDefault(); const goal = $('#goal').value.trim(); intent(goal);
    busy($('button[type=submit]', event.currentTarget), async () => {
      if (!state.me) return auth(() => startProject(goal));
      await startProject(goal);
    }, $('.form-error', event.currentTarget));
  });
}

async function startProject(goal) {
  if (!state.config.aiReady) throw new Error('AIの接続を準備しています。入力した企画はこの画面に残っています。');
  const result = await api('/projects', {goal}); clearIntent(); navigate('/workspace#project=' + result.id);
}

function auth(after = () => navigate('/workspace')) {
  const dialog = $('#auth-dialog');
  $('#auth-content').innerHTML = `<p class="eyebrow">WELCOME TO NOTTA</p><h2 id="auth-title">メールで、はじめる。</h2><p class="dialog-lead">確認コードでログインします。パスワード・カード登録は不要です。</p>
    <form id="auth-form" class="form-stack"><label>メールアドレス<input name="email" type="email" autocomplete="email" maxlength="200" required></label><label>表示名<input name="name" autocomplete="nickname" maxlength="40" placeholder="募集や参加で使う名前" required></label>
    <label class="check-label"><input type="checkbox" name="consent" required><span>18歳以上で、<a href="/terms" target="_blank" rel="noopener">利用規約</a>と<a href="/privacy" target="_blank" rel="noopener">個人情報の取扱い</a>に同意します。</span></label>
    <p class="form-error" role="alert"></p><button class="button" type="submit" ${state.config.mailReady ? '' : 'disabled'}>確認コードを送る</button></form>
    ${state.config.mailReady ? '' : '<p class="hint">メール送信の設定中です。ご入力の企画はこの画面に残っています。</p>'}`;
  if (!dialog.open) dialog.showModal();
  $('#auth-form').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget, values = new FormData(form);
    busy($('button[type=submit]', form), async () => {
      const email = values.get('email').trim(); const request = {email, name:values.get('name').trim(), consent:values.get('consent') === 'on'};
      const result = await api('/auth/start', request); codeForm(result, email, request, after);
    }, $('.form-error', form));
  });
}
function codeForm(result, email, request, after) {
  $('#auth-content').innerHTML = `<p class="eyebrow">CHECK YOUR EMAIL</p><h2 id="auth-title">確認コードを入力</h2><p class="dialog-lead">${esc(email)} にお送りしました。<br>メールにある6桁の数字を入力してください。</p>
    ${result.delivery === 'unknown' ? `<p class="banner warning">${esc(result.message)}</p>` : ''}
    <form id="code-form" class="form-stack"><label>確認コード<input class="code-input" name="code" inputmode="numeric" pattern="[0-9]{6}" minlength="6" maxlength="6" autocomplete="one-time-code" required></label><p class="form-error" role="alert"></p><button class="button" type="submit">ログインして続ける</button></form>
    <div class="dialog-footer"><button class="text-button" id="resend-code">メールを再送する</button><button class="text-button" id="change-email">アドレスを変更</button><p class="hint">有効期間は10分です。届かない場合は迷惑メールも確認してください。</p></div>`;
  $('[name=code]', $('#code-form')).focus();
  $('#code-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
    busy($('button', form), async () => { await api('/auth/verify', {challenge:result.challenge, code:form.elements.code.value}); await refreshMe(); $('#auth-dialog').close(); await after(); }, $('.form-error', form));
  });
  $('#resend-code').addEventListener('click', event => busy(event.currentTarget, async () => { const next = await api('/auth/start', request); codeForm(next, email, request, after); notice('確認メールを再送しました。'); }));
  $('#change-email').addEventListener('click', () => auth(after));
}

function contractDetails() {
  return `<ul><li>月額3,000円（税込総額）。申込み時に初回決済、以後毎月自動更新。</li><li>同時に1企画。募集公開、応募者比較、担当決定、提出・確認・貢献記録。</li><li>AIとの対話は契約月100回、検索10回まで。企画制作や運営代行は別途相談。</li><li>マイページで次回更新を停止できます。支払済み期間の終了まで利用できます。</li><li>初回支払いから7日以内の申出で全額返金。返金時は有料機能を終了します。</li></ul>`;
}
async function offer() {
  if (!state.me) return auth(offer);
  if (state.dirty) await saveProject();
  const dialog = $('#confirm-dialog');
  $('#confirm-content').innerHTML = `<p class="eyebrow">CREATE TOGETHER</p><h2 id="confirm-title">募集と共同作業へ。</h2><p class="dialog-lead">無料で作った企画を、そのまま使えます。</p><h3 class="price">3,000<span>円／月・税込総額</span></h3>${contractDetails()}
    <form id="checkout-form" class="form-stack"><label class="check-label"><input type="checkbox" name="consent" required><span>上記の料金・自動更新・解約・返金条件、<a href="/terms" target="_blank" rel="noopener">利用規約</a>、<a href="/legal" target="_blank" rel="noopener">販売者情報</a>を確認しました。</span></label><p class="form-error" role="alert"></p><button class="button" type="submit" ${state.config.billingReady ? '' : 'disabled'}>Stripeで月額3,000円を申し込む ↗</button></form>
    <p class="hint">${state.config.billingReady ? (state.config.billingEnvironment === 'sandbox' ? '検証用の決済です。実際のお金は請求されません。' : 'Stripeの安全な決済ページに移動します。カード情報はノッタに保存しません。') : '月額プランの決済は準備中です。現在は請求されません。'}</p>`;
  dialog.showModal();
  $('#checkout-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
    busy($('button', form), async () => { const result = await api('/billing/checkout', {consent:form.elements.consent.checked,terms:state.config.terms}); state.dirty = false; location.assign(result.url); }, $('.form-error', form));
  });
}

function billingView(billing) {
  const sub = billing.subscription;
  return `<section class="billing-box" id="billing"><div class="list-heading"><h2>契約とお支払い</h2><span class="pill">${billing.paid ? '月額プラン' : '無料'}</span></div>
    ${sub ? `<p>状態：${esc(sub.refunded ? '返金に伴い利用終了' : sub.cancel_at ? '次回更新を停止' : billing.paid ? '利用中' : '支払い・契約を確認してください')}<br>支払済みの利用期間：${date(sub.paid_until)} まで${sub.cancel_at ? '<br>次回の自動更新はありません。' : ''}</p>` : '<p>企画の準備は無料です。募集の公開と共同作業から月額3,000円です。</p>'}
    ${billing.pending ? '<p class="banner warning">決済結果を確認しています。重複して申し込まず、下のボタンで状態を確認してください。</p>' : ''}
    ${billing.refundRequest ? `<p class="banner">${billing.refundRequest.status === 'completed' ? 'Stripeで全額返金の処理を確認しました。明細への反映日はカード会社によって異なります。' : `返金のお申出を受け付けています。受付日：${date(billing.refundRequest.requested)}。MAORAからメールでご連絡します。`}</p>` : ''}
    <div class="toolbar">${!sub && !billing.pending ? '<button class="button compact" data-action="offer">月額プランの内容を見る</button>' : ''}
    ${state.config.billingReady && (sub || billing.pending) ? '<button class="button secondary compact" data-action="sync">支払い状態を確認</button>' : ''}
    ${billing.pendingUrl ? `<a class="button secondary compact" href="${esc(billing.pendingUrl)}" rel="noreferrer">未払いの申込みを再開 ↗</a><p class="hint">すでに支払った場合は再決済せず、支払い状態を確認してください。未払いの申込みを取り消したい場合はお問い合わせください。</p>` : ''}
    ${sub && billing.paid && !sub.cancel_at ? '<button class="button secondary compact" data-action="cancel-subscription">次回更新を停止する</button>' : ''}
    ${billing.refundEligible && !billing.refundRequest ? '<button class="button secondary compact" data-action="refund">初回の全額返金を申し出る</button>' : ''}</div><p class="cancel-note">ご相談：<a href="mailto:shumpei.hasegawa@maora.co.jp">shumpei.hasegawa@maora.co.jp</a></p></section>`;
}
function confirmAction(title, body, label, action) {
  const dialog = $('#confirm-dialog');
  $('#confirm-content').innerHTML = `<h2 id="confirm-title">${esc(title)}</h2><p class="dialog-lead">${esc(body)}</p><form id="confirmation-form" class="form-stack"><label class="check-label"><input type="checkbox" required><span>内容を確認しました。</span></label><p class="form-error" role="alert"></p><button class="button" type="submit">${esc(label)}</button></form>`;
  dialog.showModal();
  $('#confirmation-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
    busy($('button', form), async () => { await action(); dialog.close(); await render(); }, $('.form-error', form));
  });
}

function dashboard() {
  state.project = null; state.dirty = false;
  document.title = 'マイページ｜ノッタ';
  const me = state.me, active = me.projects.find(p => p.state !== 'archived');
  main(`<div class="workspace"><div class="workspace-top"><div><p class="eyebrow">YOUR PROJECTS</p><h1>${esc(me.user.name)}さんのマイページ</h1></div><button class="text-button" data-action="logout">ログアウト</button></div>
    ${location.pathname === '/billing/return' ? '<div class="banner"><strong>お支払いの状態を照合します。</strong>反映に少し時間がかかる場合があります。重複して申し込まず「支払い状態を確認」を押してください。</div>' : ''}
    <section><div class="list-heading"><h2>自分の企画</h2>${!active ? '<button class="button secondary compact" data-action="new-project">企画をつくる</button>' : ''}</div>
    ${me.projects.length ? `<div class="project-list">${me.projects.map(p => `<article class="project-card"><span class="pill">${esc(p.state === 'published' ? '募集中' : p.state === 'archived' ? '完了' : '準備中・非公開')}</span><h3>${esc(p.title)}</h3><p>更新：${date(p.updated)}</p><a class="button secondary compact" href="/workspace#project=${p.id}">${p.state === 'archived' ? '企画と記録を見る' : '企画を開く'} →</a></article>`).join('')}</div>` : '<div class="empty">思いついたことを、まずひとつ。AIと企画や役割を整理できます。<br><a href="/#goal">無料で企画をつくる →</a></div>'}</section>
    <section class="dashboard-section"><h2>参加している企画</h2><p class="hint">参加者の利用は無料です。応募内容や提出物は、ご本人と企画者が確認できます。</p>
      ${me.applications.length ? `<div class="application-grid">${me.applications.map(fanApplication).join('')}</div>` : '<div class="empty">募集ページから応募すると、ここに参加状況が表示されます。</div>'}</section>
    ${me.projects.length || me.billing.subscription || me.billing.pending ? billingView(me.billing) : '<p class="hint">参加者への課金はありません。ご自身で企画を募集する場合に、月額プランを選べます。</p>'}</div>`);
  wireApplications();
}
function contribution(a) {
  if (a.status !== '確認済') return '';
  return `<div class="contribution"><h4>企画者が確認した貢献</h4><p>${esc(a.confirmation)}</p><p class="hint">確認日：${date(a.confirmed_at)}／ご本人と企画者のみ閲覧できます。</p></div>`;
}
function fanApplication(a) {
  return `<article class="application-card" data-application="${a.id}" data-version="${a.version}"><div class="application-top"><div><h3>${esc(a.title)}</h3><p class="muted">企画者：${esc(a.creator)}／役割：${esc(a.role)}</p></div><span class="pill">${esc(a.status)}</span></div>
    <details><summary>応募した内容</summary><div class="text-block">${esc(a.experience)}</div></details>
    ${a.submission ? `<p class="hint">提出した内容</p><div class="text-block">${linkedText(a.submission)}</div>` : ''}
    ${a.feedback ? `<p class="hint">企画者からのコメント</p><div class="text-block">${esc(a.feedback)}</div>` : ''}${contribution(a)}
    ${['担当','差戻し'].includes(a.status) && a.state !== 'archived' ? '<form data-operation="submit"><label>成果を提出（文章・URL）<textarea name="body" maxlength="4000" required placeholder="取り組んだ内容と、成果が確認できる文章やURLを記入してください。"></textarea></label><p class="privacy-note">共有するURLの公開範囲を確認してください。カード情報や機密情報は記入しないでください。</p><p class="form-error" role="alert"></p><button class="button compact" type="submit">企画者に提出する</button></form>' : ''}
    ${a.status !== '取消' ? '<div class="toolbar"><button class="text-button" data-operation="cancel">参加・応募を取り消す</button></div>' : ''}</article>`;
}

const fields = [['title','企画の名前',80],['goal','企画の目的',1000],['success','達成したと判断する条件',1000],['output','最初につくる成果物',1000],['schedule','日程と進め方',1500],['conditions','参加条件・権利・報酬など',2000],['recruitment','募集文',3000]];
function quotaText(q) { return `AIの対話 ${q.repliesUsed} / ${q.repliesLimit}回　検索 ${q.searchesUsed} / ${q.searchesLimit}回`; }
async function projectPage(id) {
  state.project = await api('/projects/' + id); state.dirty = false;
  const p = state.project, d = p.draft, locked = p.state === 'archived' || p.applications.length > 0;
  document.title = d.title + '｜ノッタ';
  main(`<div class="workspace"><p class="breadcrumb"><a href="/workspace">マイページ</a> / 自分の企画</p><div class="workspace-top"><div><span class="pill">${p.state === 'published' ? '募集中' : p.state === 'archived' ? '完了' : '準備中・非公開'}</span><h1>${esc(d.title)}</h1></div><div class="toolbar"><button class="button secondary compact" data-action="copy">原稿をコピー</button>${p.state === 'published' ? `<a class="button secondary compact" href="/p/${p.slug}" target="_blank" rel="noopener">募集ページ ↗</a>` : ''}</div></div>
    ${!p.paid && p.state !== 'archived' ? '<div class="banner"><strong>企画の準備は無料です。</strong>内容を確かめて、自分の言葉に整えましょう。募集の公開と共同作業から月額3,000円です。</div>' : ''}
    ${locked ? '<div class="banner">応募を受け付けた企画と完了した企画は、募集条件を固定しています。原稿や記録は引き続き確認できます。</div>' : ''}
    <div class="tabs" role="tablist" aria-label="企画の画面"><button class="tab" role="tab" aria-selected="${state.tab === 'draft'}" data-tab="draft">企画と原稿</button><button class="tab" role="tab" aria-selected="${state.tab === 'applications'}" data-tab="applications">応募・共同作業（${p.applications.length}）</button></div>
    <div id="draft-tab" role="tabpanel" ${state.tab === 'draft' ? '' : 'hidden'}><div class="workspace-layout"><section class="panel"><div class="panel-head"><h2>AIと企画を整える</h2><span class="seal">相談ノート</span></div><div class="chat-log" id="chat-log" aria-live="polite">${p.messages.length ? p.messages.map(m => `<div class="chat-message ${m.role === 'user' ? 'user' : ''}"><span class="chat-role">${m.role === 'user' ? 'あなた' : 'ノッタ'}</span>${esc(m.body)}</div>`).join('') : '<p class="hint">やりたいことを教えてください。目的・達成条件・役割・募集文を、ご自身の企画に合わせてつくります。</p>'}</div>
    <form id="chat-form" class="chat-form"><label>相談したいこと<textarea name="prompt" maxlength="1500" required ${locked ? 'disabled' : ''} placeholder="例：初めて参加する人でも取り組みやすい役割にしたい">${p.messages.length ? '' : esc('この企画を具体化してください：' + d.goal)}</textarea></label>
    ${state.config.searchReady && !locked ? '<details><summary class="small">公開情報を検索して参考にする</summary><label class="hint">外部検索へ送る語句<input name="searchQuery" maxlength="160" placeholder="例：ZINE 小冊子 共同制作 事例"></label><label class="check-label"><input type="checkbox" name="search"><span>入力した検索語句をGoogle Searchへ送ることに同意します。個人名や連絡先などは含めないでください。</span></label></details>' : ''}
    <p class="quota-line">${quotaText(p.quota)}</p><p class="privacy-note">企画と会話の内容をGoogleのAIへ送信します。個人情報・秘密情報は入力しないでください。AIの案は、公開前にご自身で確認してください。</p><p class="form-error" role="alert"></p><p id="ai-progress" class="busy-indicator" role="status" hidden>企画を整理しています。画面を開いたままお待ちください…</p>
    <button class="button" type="submit" ${locked || !state.config.aiReady || p.quota.repliesUsed >= p.quota.repliesLimit ? 'disabled' : ''}>${p.messages.length ? 'AIと相談する' : '最初の企画案をつくる'} ↗</button></form>
    ${!state.config.aiReady ? '<p class="hint">AIの接続を準備しています。企画は保存されています。</p>' : ''}${sourcesView(d.sources)}</section>
    <section class="panel"><div class="panel-head"><h2>企画と募集の原稿</h2><span class="small muted">手編集は回数制限なし</span></div><form id="editor-form"><div class="editor-grid">${fields.map(([key,label,max]) => `<label>${label}${key === 'title' ? `<input name="${key}" maxlength="${max}" value="${esc(d[key])}" required ${locked ? 'readonly' : ''}>` : `<textarea name="${key}" maxlength="${max}" ${['goal','success'].includes(key) ? 'required' : ''} ${locked ? 'readonly' : ''} ${key === 'recruitment' ? 'class="long"' : ''}>${esc(d[key])}</textarea>`}${key === 'conditions' ? '<p class="field-note">報酬の有無、権利、連絡方法、参加条件を公開前に確認してください。</p>' : ''}</label>`).join('')}
    <div><label>協力してほしい役割（2〜3種類）</label><div id="role-editors">${Array.from({length:3}, (_,i) => `<div class="role-editor"><label>役割${i+1}<input name="role${i}" maxlength="80" value="${esc(d.roles[i]?.title || '')}" ${locked ? 'readonly' : ''} placeholder="役割の名前"></label><label>この役割でつくるもの<textarea name="roleOutput${i}" maxlength="500" ${locked ? 'readonly' : ''}>${esc(d.roles[i]?.output || '')}</textarea></label></div>`).join('')}</div></div>
    <label>応募時に聞く項目（1行に1つ・5項目まで）<textarea name="questions" ${locked ? 'readonly' : ''}>${esc(d.questions.join('\n'))}</textarea></label><label>これから確かめること（1行に1つ・5項目まで）<textarea name="unknowns" ${locked ? 'readonly' : ''}>${esc(d.unknowns.join('\n'))}</textarea></label></div><p class="form-error" role="alert"></p>
    ${!locked ? '<div class="editor-actions"><button class="button secondary" type="submit">原稿を保存</button><button class="button" type="button" data-action="publish">募集を公開する ↗</button><span class="save-state" id="save-state">保存済み</span></div><p class="hint">原稿の変更を保存すると非公開に戻ります。内容を確認してから公開してください。</p>' : ''}</form></section></div></div>
    <div id="applications-tab" role="tabpanel" ${state.tab === 'applications' ? '' : 'hidden'}>${applicationsView(p)}</div>
    ${p.state !== 'archived' ? '<div class="dashboard-section"><button class="text-button" data-action="archive">この企画を完了する</button><p class="hint">完了後も企画と記録を閲覧できます。同時に進められる企画は1件です。</p></div>' : ''}</div>`);
  if (p.groundingRequestId) showGrounding(p.groundingRequestId);
  const editor = $('#editor-form'); editor.addEventListener('input', () => { state.dirty = true; if ($('#save-state')) $('#save-state').textContent = '未保存の変更があります'; });
  editor.addEventListener('submit', event => { event.preventDefault(); busy($('button[type=submit]', editor), async () => { await saveProject(); await projectPage(id); notice('原稿を保存しました。'); }, $('.form-error', editor)); });
  $('#chat-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
    busy($('button[type=submit]', form), async () => {
      if (state.busy) return; const values = new FormData(form); state.busy = true; $('#ai-progress').hidden = false;
      try { if (state.dirty) await saveProject(); await api('/projects/' + id + '/assist', {requestId:crypto.randomUUID(),version:state.project.version,prompt:values.get('prompt'),search:values.get('search') === 'on',searchConsent:values.get('search') === 'on',searchQuery:values.get('searchQuery') || ''}); await projectPage(id); notice('企画案を保存しました。内容を確認して編集できます。'); }
      finally { state.busy = false; if ($('#ai-progress')) $('#ai-progress').hidden = true; }
    }, $('.form-error', form));
  });
  $$('.tab').forEach((tab, index, tabs) => {
    tab.id = tab.dataset.tab + '-label'; tab.setAttribute('aria-controls', tab.dataset.tab + '-tab'); tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1;
    $('#' + tab.dataset.tab + '-tab').setAttribute('aria-labelledby',tab.id);
    tab.addEventListener('click', () => { state.tab = tab.dataset.tab; tabs.forEach(t => { t.setAttribute('aria-selected',String(t === tab)); t.tabIndex = t === tab ? 0 : -1; }); $('#draft-tab').hidden = state.tab !== 'draft'; $('#applications-tab').hidden = state.tab !== 'applications'; });
    tab.addEventListener('keydown', event => { if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { event.preventDefault(); const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length]; next.click(); next.focus(); } });
  });
  wireApplications(); const log = $('#chat-log'); log.scrollTop = log.scrollHeight;
}
function collectDraft() {
  const form = $('#editor-form'), result = {};
  fields.forEach(([key]) => { result[key] = form.elements[key].value.trim(); });
  result.roles = Array.from({length:3}, (_,i) => ({title:form.elements['role'+i].value.trim(),output:form.elements['roleOutput'+i].value.trim()})).filter(r => r.title || r.output);
  ['questions','unknowns'].forEach(key => { result[key] = form.elements[key].value.split('\n').map(x => x.trim()).filter(Boolean); });
  return result;
}
async function saveProject() {
  if (!state.dirty) return;
  const p = state.project; const draft = collectDraft();
  await api('/projects/' + p.project + '/edit', {version:p.version,draft}); state.dirty = false;
  state.project = await api('/projects/' + p.project);
  if ($('#save-state')) $('#save-state').textContent = '保存済み';
}
function sourcesView(sources = []) {
  const safe = sources.filter(source => /^https:\/\//.test(source.url));
  return `<div class="sources" id="sources">${safe.length ? `<p>検索で見つかった参照元（内容は要確認）</p><ul>${safe.map(source => `<li><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)} ↗</a></li>`).join('')}</ul>` : ''}</div>`;
}
function showGrounding(requestId) {
  const frame = document.createElement('iframe'); frame.className = 'grounding-frame'; frame.title = 'Google Searchの検索候補';
  frame.setAttribute('sandbox','allow-popups allow-popups-to-escape-sandbox');
  frame.src = '/api/service/grounding/' + encodeURIComponent(requestId);
  $('#sources').append(frame);
}
function applicationsView(p) {
  if (!p.paid && p.state !== 'archived') return '<div class="banner"><strong>募集と共同作業は月額プランで。</strong>保存した原稿をそのまま使い、参加者と進められます。<div class="toolbar"><button class="button compact" data-action="offer">月額3,000円の内容を見る</button></div></div>';
  if (!p.applications.length) return `<div class="empty">${p.state === 'published' ? '募集ページのURLをご自身のSNSなどで案内してください。応募が届くと、ここで比較・担当決定できます。' : '原稿を確認して、募集ページを公開しましょう。'}${p.state === 'published' ? `<p><a href="/p/${p.slug}" target="_blank" rel="noopener">募集ページを開く ↗</a></p>` : ''}</div>`;
  return `<div class="panel comparison"><h2>応募者を比較する</h2><div class="table-wrap"><table><thead><tr><th>表示名</th><th>役割</th><th>応募内容</th><th>状態</th></tr></thead><tbody>${p.applications.map(a => `<tr><td>${esc(a.name)}</td><td>${esc(p.roles.find(r => r.id === a.task)?.title)}</td><td>${esc(a.experience)}</td><td>${esc(a.status)}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="application-grid">${p.applications.map(a => `<article class="application-card" data-application="${a.id}" data-version="${a.version}"><div class="application-top"><div><h3>${esc(a.name)}</h3><p class="muted">役割：${esc(p.roles.find(r => r.id === a.task)?.title)}</p></div><span class="pill">${esc(a.status)}</span></div><div class="text-block">${esc(a.experience)}</div>
    ${a.submission ? `<p class="hint">提出された成果</p><div class="text-block">${linkedText(a.submission)}</div>` : ''}${a.feedback ? `<p class="hint">伝えたコメント</p><div class="text-block">${esc(a.feedback)}</div>` : ''}${contribution(a)}
    ${p.state !== 'archived' && ['応募','候補','提出'].includes(a.status) ? `<form data-operation="status"><label>次の状態<select name="status">${(a.status === '提出' ? ['確認済','差戻し'] : a.status === '候補' ? ['担当','見送り'] : ['候補','担当','見送り']).map(t => `<option value="${t}">${t === '確認済' ? '成果を確認して貢献を記録' : t === '担当' ? '担当をお願いする' : t}</option>`).join('')}</select></label><label>${a.status === '提出' ? '確認したこと・修正してほしいこと（必須）' : '応募者へのコメント（任意）'}<textarea name="comment" maxlength="1000" ${a.status === '提出' ? 'required' : ''}></textarea></label><p class="form-error" role="alert"></p><button class="button compact" type="submit">保存して進める</button></form>` : ''}</article>`).join('')}</div>`;
}
function wireApplications() {
  $$('[data-application]').forEach(card => {
    $$('form[data-operation]', card).forEach(form => form.addEventListener('submit', event => {
      event.preventDefault(); const values = new FormData(form), operation = form.dataset.operation;
      busy($('button[type=submit]', form), async () => { await api('/applications/' + card.dataset.application + '/' + operation, {version:Number(card.dataset.version), body:values.get('body'),status:values.get('status'),comment:values.get('comment') || ''}); await render(); notice(operation === 'submit' ? '成果を提出しました。' : '応募の状態を更新しました。'); }, $('.form-error', form));
    }));
    $('[data-operation=cancel]', card)?.addEventListener('click', () => confirmAction('参加・応募を取り消す', '企画者に取消を表示し、確認済みの貢献記録は取り下げます。', '取り消す', () => api('/applications/' + card.dataset.application + '/cancel', {version:Number(card.dataset.version)})));
  });
}

async function publicPage(slug) {
  const p = await api('/public/' + slug); state.public = p;
  document.title = p.title + '｜ノッタの募集';
  main(`<article class="public-page"><p class="eyebrow">LET'S CREATE TOGETHER</p><span class="pill">ファン・協力者の参加は無料</span><h1>${esc(p.title)}</h1><p class="public-intro">企画者：${esc(p.creator)}<br>参加条件を確認して、ご自身に合う役割へご応募ください。</p>
    ${[['goal','企画の目的'],['success','目指すこと'],['output','最初につくるもの'],['schedule','日程と進め方'],['conditions','参加条件・権利・報酬など'],['recruitment','募集について']].map(([key,label]) => `<section class="public-section"><h2>${label}</h2><p>${esc(p[key])}</p></section>`).join('')}
    <section class="public-section"><h2>一緒につくる役割</h2><div class="public-roles">${p.roles.map(role => `<article class="public-role"><h3>${esc(role.title)}</h3><p>${esc(role.output)}</p><button class="button secondary" data-role="${role.id}">この役割に応募する →</button></article>`).join('')}</div></section><p class="privacy-note">ノッタは企画の募集・共同作業を支援します。集客・成果の保証や企画の運営代行は行いません。報酬や権利などは企画者と確認してからご参加ください。</p></article>`);
  $$('[data-role]').forEach(button => button.addEventListener('click', () => { if (!state.me) return auth(() => applyDialog(button.dataset.role)); applyDialog(button.dataset.role); }));
}
function applyDialog(roleId) {
  const p = state.public, role = p.roles.find(r => r.id === roleId), dialog = $('#confirm-dialog');
  $('#confirm-content').innerHTML = `<p class="eyebrow">JOIN THE PROJECT</p><h2 id="confirm-title">${esc(role.title)}に応募</h2><p class="dialog-lead">${esc(p.title)}</p><form id="apply-form" class="form-stack"><label>参加したい理由・できること<textarea name="experience" maxlength="2000" required></textarea></label>${p.questions.length ? `<p class="hint">あわせて教えてください：<br>${p.questions.map(esc).join('<br>')}</p>` : ''}<label class="check-label"><input type="checkbox" required><span>日程・参加条件・権利・報酬などを確認しました。応募内容と提出物は、ご本人と企画者が確認できます。貢献の記録は企画者が成果を確認して作成します。</span></label><p class="form-error" role="alert"></p><button class="button" type="submit">無料で応募する</button></form>`;
  dialog.showModal();
  $('#apply-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
    busy($('button', form), async () => { await api('/projects/' + p.id + '/apply', {task:roleId,experience:form.elements.experience.value,consent:true}); dialog.close(); navigate('/workspace'); }, $('.form-error', form));
  });
}

function legalPage(kind) {
  const pages = {
    terms: ['利用規約', `<p>2026年10月23日制定。MAORA株式会社（以下「当社」）が提供するノッタをご利用になる前にお読みください。</p><h2>1. 提供するサービス</h2><p>企画の整理、AIとの対話、原稿の保存・編集、参加者の募集と共同作業を支援します。AIの出力には誤りや第三者の権利を侵害する内容が含まれる場合があります。ご自身で確認してご利用ください。集客、人数、収益、成果を保証しません。</p><h2>2. 無料体験と月額プラン</h2><p>無料体験は1人1企画、AIとの対話10回・検索1回が上限です。月額プランは総額3,000円（税込）で毎月自動更新し、同時に1企画を進められます。完了後に次の企画を作成できます。AIとの対話100回・検索10回を各契約月の上限として提供します。人による企画制作、集客や運営代行は含みません。初期の受付上限は無料30人・有料5人です。全体の利用枠に応じて新規受付を調整します。自動で有料へ移行することはありません。</p><h2>3. お支払い・更新停止・返金</h2><p>Stripeの決済ページでお支払いいただきます。申込み時に初回決済、以後毎月同じ日を基準に更新します。同じ日がない月は月末が基準です。マイページから次回更新を停止でき、支払済み期間の終了まで利用できます。初回支払いから7日以内にマイページまたは問い合わせ先へ申し出た場合、初回料金を全額返金します。返金時は有料機能の利用を終了します。通常の更新停止は日割り返金を伴いません。法令に基づく権利を制限するものではありません。</p><h2>4. 企画と参加</h2><p>公開、応募、担当決定、提出、成果確認は利用者ご自身が操作します。参加者の利用は無料です。企画者は日程、報酬の有無、成果物の権利、連絡方法などの条件を明確にし、参加者と合意してください。応募後は募集条件を固定します。成果の確認は企画者が行い、確認済みの内容を本人と企画者に表示します。参加者は応募・参加を取り消せます。</p><h2>5. 投稿とアカウント</h2><p>正しい連絡先を登録し、メールの確認コードを他人と共有しないでください。投稿の権利は利用者または正当な権利者に帰属します。当社には、本サービスの提供に必要な範囲で保存・処理・表示する権限を許諾いただきます。他人の権利侵害、違法行為、嫌がらせ、なりすまし、秘密情報・決済情報の投稿、不正アクセスや過度な利用を禁止します。本サービスは18歳以上の方がご利用いただけます。</p><h2>6. サービスの停止・責任</h2><p>保守、障害、不正利用、外部サービスの停止により、一時的に機能を制限する場合があります。事業者の故意・重過失など、法令上免責できない責任を免除するものではありません。重要な変更やサービス終了は、利用者への案内と未利用期間の対応を含めてお知らせします。</p><h2>7. お問い合わせ</h2><p>MAORA株式会社（マオラ）<br><a href="mailto:shumpei.hasegawa@maora.co.jp">shumpei.hasegawa@maora.co.jp</a></p>`],
    privacy: ['プライバシーポリシー', `<p>2026年10月23日制定。MAORA株式会社（マオラ）は、ノッタにおいて以下のとおり個人情報を取り扱います。</p><h2>取得する情報と目的</h2><p>メールアドレス、表示名、認証の日時、企画・対話・応募・提出・確認内容、契約と支払いの状態を、本人認証、サービス提供、問い合わせ対応、請求管理に使用します。認証の不正利用を防ぐためIPアドレスを秘密鍵で変換した値と試行回数を記録します。画面の利用状況は、利用者ID・操作の種類・日時など必要最小限の情報で記録します。広告目的の追跡は行いません。</p><h2>公開される範囲</h2><p>下書き、AIとの会話、未確認メモは本人のみ閲覧できます。募集を公開すると、企画概要、条件、役割、募集文、表示名を公開します。応募内容・提出物・確認コメントは応募した本人と企画者に表示します。メールアドレスを募集ページや応募者一覧に表示することはありません。参加を取り消すと、確認済みの貢献記録を取り下げます。</p><h2>外部サービスへの送信</h2><p>AIとの相談では、企画原稿と相談内容をGoogleのGemini APIへ送信します。検索を選択した場合は、確認した検索語句のみをGoogle Searchへ送信します。メール送信にはResend、データ保存にはNeon、サイト配信にはVercelを利用します。各事業者が国外で処理する場合があります。決済はStripeが取り扱い、ノッタはカード番号やセキュリティコードを取得・保存しません。APIの利用枠や事業者の規約に従って処理します。秘密情報や不要な個人情報は入力しないでください。</p><h2>保管・安全管理</h2><p>通信を暗号化し、権限確認とログイン用Cookieを使用します。認証コードは10分で失効し、ハッシュとして保管します。会計記録など保存義務のある情報は必要な期間保管します。その他の情報はサービス提供に必要な範囲で保管し、退会・削除のご相談には本人確認のうえ対応します。アクセス情報の保管期間や削除の運用を変更する場合は、このページでお知らせします。</p><h2>開示・訂正・削除のお問い合わせ</h2><p>MAORA株式会社（マオラ）<br>東京都千代田区内神田1-9-12 J.NODE 内神田5階<br><a href="mailto:shumpei.hasegawa@maora.co.jp">shumpei.hasegawa@maora.co.jp</a></p>`],
    legal: ['特定商取引法に基づく表記', `<dl><dt>販売事業者</dt><dd>MAORA株式会社（マオラ）</dd><dt>運営責任者</dt><dd>長谷川峻平</dd><dt>所在地</dt><dd>東京都千代田区内神田1-9-12 J.NODE 内神田5階</dd><dt>連絡先</dt><dd><a href="mailto:shumpei.hasegawa@maora.co.jp">shumpei.hasegawa@maora.co.jp</a><br>${state.config.phone ? esc(state.config.phone) : '電話窓口は掲載準備中です。有料申込みの受付前に表示します。'}<br><a href="https://www.maora.co.jp/" target="_blank" rel="noopener noreferrer">会社公式サイト ↗</a></dd><dt>販売価格</dt><dd>月額3,000円（税込総額）。無料体験は0円です。</dd><dt>追加の費用</dt><dd>サービスの利用に必要な通信費はお客様のご負担です。制作や運営代行の個別相談は月額料金に含みません。</dd><dt>お支払方法・時期</dt><dd>Stripeによるカード決済。申込み時に初回決済、以後毎月自動更新します。同じ日がない月は月末を基準に更新します。</dd><dt>利用開始</dt><dd>決済の完了を確認した後、有料機能を利用できます。反映に時間がかかる場合は、追加購入せずお問い合わせください。</dd><dt>更新停止</dt><dd>マイページから次回の更新を停止できます。支払済み期間の終了まで有料機能を利用できます。</dd><dt>返金</dt><dd>初回支払いから7日以内にマイページまたはメールで申し出た場合、初回料金を全額返金します。返金は原則として元のお支払方法に行い、反映日はカード会社によって異なります。通常の更新停止は日割り返金を伴いません。</dd><dt>提供範囲</dt><dd>同時に1企画。企画準備、募集公開、応募比較、担当決定、文章・URLによる提出、確認と貢献記録。企画の集客や人による制作・運営代行は別途相談です。</dd><dt>利用環境</dt><dd>インターネットに接続できる、最新版のChrome・Safari・Edge等のブラウザー。無料体験で動作をお確かめください。</dd></dl>`]
  };
  const [title, body] = pages[kind]; document.title = title + '｜ノッタ'; main(`<article class="legal-page"><p class="breadcrumb"><a href="/">ホーム</a></p><h1>${title}</h1>${body}</article>`);
}

async function copyProject() {
  const d = state.dirty ? collectDraft() : state.project.draft;
  const body = fields.map(([key,label]) => label + '\n' + d[key]).concat(['協力者の役割\n' + d.roles.map(r => r.title + '：' + r.output).join('\n'),'応募時に聞く項目\n' + d.questions.join('\n'),'これから確かめること\n' + d.unknowns.join('\n')]).join('\n\n');
  try { await navigator.clipboard.writeText(body); notice('企画と募集の原稿をコピーしました。'); }
  catch { const dialog = $('#confirm-dialog'); $('#confirm-content').innerHTML = '<h2 id="confirm-title">原稿をコピー</h2><p class="dialog-lead">下の原稿を選択してコピーしてください。</p><textarea id="copy-area" rows="12" readonly></textarea>'; $('#copy-area').value = body; dialog.showModal(); $('#copy-area').select(); }
}
document.addEventListener('click', event => {
  const close = event.target.closest('[data-close]'); if (close) close.closest('dialog').close();
  const button = event.target.closest('[data-action]'); if (!button) return;
  const action = button.dataset.action;
  if (action === 'workspace') { if (state.me) navigate('/workspace'); else $('#goal').focus(); }
  if (action === 'offer') busy(button, offer);
  if (action === 'logout') busy(button, async () => { await api('/logout', {}); state.me = null; navigate('/'); });
  if (action === 'new-project') navigate('/#goal');
  if (action === 'copy') busy(button, copyProject);
  if (action === 'sync') busy(button, async () => { await api('/billing/sync', {}); await render(); notice('支払い状態を確認しました。'); });
  if (action === 'cancel-subscription') confirmAction('次回更新を停止する', '支払済みの利用期間が終わるまでは、有料機能を引き続き使えます。次回の自動更新を停止します。', '次回更新を停止', () => api('/billing/cancel', {confirm:true}));
  if (action === 'refund') confirmAction('初回の全額返金を申し出る', '初回支払いから7日以内の全額返金を受け付け、次回更新を停止します。MAORAが処理を確認してご連絡します。', '返金を申し出る', async () => { const result = await api('/billing/refund', {confirm:true}); notice(result.message); });
  if (action === 'archive') confirmAction('この企画を完了する', '募集を終了し、応募や提出などの更新を止めます。企画と記録は引き続き確認できます。', '企画を完了する', () => api('/projects/' + state.project.project + '/archive', {version:state.project.version,confirm:true}));
  if (action === 'publish') busy(button, async () => {
    if (state.dirty) await saveProject(); if (!state.project.paid) return offer();
    confirmAction('募集ページを公開する', '目的・条件・役割・募集文・表示名を公開します。報酬、権利、日程、AIが作成した内容を確認してください。応募後は募集条件を固定します。', '確認して公開', () => api('/projects/' + state.project.project + '/publish', {version:state.project.version,consent:true}));
  });
});
$('#account-button').addEventListener('click', () => { if (state.me) navigate('/workspace'); else auth(); });
window.addEventListener('beforeunload', event => { if (state.dirty || state.busy) { event.preventDefault(); event.returnValue = ''; } });
async function render() {
  await refreshMe();
  const path = location.pathname;
  if (['/terms','/privacy','/legal'].includes(path)) return legalPage(path.slice(1));
  if (path.startsWith('/p/')) return publicPage(path.split('/')[2]);
  if (path === '/workspace' || path === '/billing/return') {
    if (!state.me) { main('<div class="error-page"><p class="eyebrow">YOUR WORKSPACE</p><h1>企画の続きを、ここから。</h1><p>メール認証でログインすると、保存した企画や参加状況を確認できます。</p><button class="button" id="workspace-login">ログインする</button></div>'); $('#workspace-login').addEventListener('click', () => auth(render)); return; }
    const id = new URLSearchParams(location.hash.slice(1)).get('project');
    if (id && /^[a-f0-9]{24}$/.test(id)) return projectPage(id);
    dashboard(); return;
  }
  landing();
}
async function boot() {
  try { state.config = await api('/config'); sandboxNotice(); await render(); }
  catch (error) { main(`<div class="error-page"><h1>画面を開けませんでした。</h1><p>${esc(error.message)}</p><a href="/" class="button secondary">ホームに戻る</a></div>`); }
}
boot();

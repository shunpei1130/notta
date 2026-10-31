'use strict';
// Initial release: AI asks questions; the operator writes the result.
window.nottaIntake = (() => {
  const contact = '<a href="mailto:shumpei.hasegawa@maora.co.jp">shumpei.hasegawa@maora.co.jp</a>';
  const labels = {interview:'ヒアリング中', submitted:'タスク分解の受付完了', working:'タスクを整理しています', ready:'タスク分解が完了'};
  const mailLabels = {pending:'未送信', sending:'送信中・結果待ち', sent:'送信サービスで受付済み', unknown:'送信結果を確認できません', failed:'送信できませんでした'};
  const time = n => n ? new Date(n * 1000).toLocaleString('ja-JP', {timeZone:'Asia/Tokyo'}) : '—';
  const draftKey = item => 'notta-answer-' + state.me.user.id + '-' + item.id + '-' + item.step;
  const readLocal = key => { try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch { return null; } };
  const writeLocal = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const clearLocal = key => { try { sessionStorage.removeItem(key); } catch {} };
  const emptyTask = () => ({title:'', instructions:'', role:'', estimate:'', completion:''});
  let adminItem = null, adminDraft = null;

  const cases = [
    {title:'ラーメン屋を開きたい',total:25,description:'物件探しから仕入れ、お店づくりまで。得意なことを持ち寄って、一杯を届けるお店をつくります。',roles:[['資金提供者',8,'開業資金を支える'],['麺仕入れ係',2,'麺の選定と仕入れ'],['食材仕入れ係',3,'スープや具材の調達'],['空き物件提供者（不動産屋）',1,'店舗候補の紹介'],['内装担当',4,'店内の設計と施工'],['接客・運営',4,'接客と店舗運営'],['広報',3,'開店のお知らせと発信']]},
    {title:'空き家をカフェにしたい',total:18,description:'使われていない家を、地域の人が集まれるカフェへ。改装から日々の運営まで協力します。',roles:[['資金提供者',5,'改装・開業資金を支える'],['空き家提供者',1,'カフェに使う場所の提供'],['改装担当',4,'空間の整備と内装'],['コーヒー・食材仕入れ係',2,'豆と食材の調達'],['接客・運営',4,'メニュー提供と店舗運営'],['広報',2,'地域へのお知らせ']]},
    {title:'地元で音楽フェスを開きたい',total:40,description:'地元の音楽と人が出会う一日を。出演者だけでなく、会場づくりや当日の案内もみんなで担います。',roles:[['資金提供者',8,'開催費用を支える'],['会場提供者',2,'開催場所の提供と調整'],['出演者',10,'演奏とパフォーマンス'],['音響・照明',4,'音と照明の準備・操作'],['設営',6,'ステージと会場の設営'],['当日運営',6,'受付と来場者の案内'],['広報',4,'告知と情報発信']]},
    {title:'自分のブランドの服をつくりたい',total:16,description:'アイデアを、実際に着られる服へ。デザインから縫製、撮影、販売まで、それぞれの得意をつなぎます。',roles:[['資金提供者',4,'制作資金を支える'],['デザイン担当',2,'服のデザインと仕様づくり'],['生地仕入れ係',2,'素材選びと調達'],['縫製担当',3,'試作と製品の縫製'],['撮影担当',2,'商品写真の撮影'],['販売・広報',3,'販売準備とブランドの発信']]}
  ];

  function openCase(index, trigger) {
    const c = cases[index];
    if (!c) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'case-dialog';
    dialog.setAttribute('aria-labelledby', 'case-title');
    dialog.innerHTML = `<button class="dialog-close" type="button" aria-label="閉じる" autofocus>×</button><span class="sample-label">モデルケース</span><h2 id="case-title">${esc(c.title)}</h2><p class="dialog-lead">${esc(c.description)}</p><p class="case-total">参加者 <strong>${c.total}</strong> 名</p><h3>参加者の内訳</h3><dl class="case-roles">${c.roles.map(([role,count,task]) => `<div><dt>${esc(role)} <b>${count}名</b></dt><dd>${esc(task)}</dd></div>`).join('')}</dl><p class="hint">企画・参加人数・役割は架空のモデルケースです。</p>`;
    const overflow = document.body.style.overflow;
    document.body.append(dialog);
    dialog.addEventListener('close', () => { document.body.style.overflow = overflow; dialog.remove(); if (trigger.isConnected) trigger.focus(); }, {once:true});
    dialog.querySelector('button').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { const r = dialog.getBoundingClientRect(); if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close(); });
    document.body.style.overflow = 'hidden';
    dialog.showModal();
  }

  function accountTools() {
    return `<div class="toolbar">${state.me?.operator ? '<a class="button secondary compact" href="/admin/intakes">運営画面</a>' : ''}<button class="text-button" data-action="logout">ログアウト</button></div>`;
  }

  function landing() {
    document.title = 'ノッタ｜つくりたいを、みんなで。';
    main(`<section class="hero intake-hero"><div class="hero-copy"><p class="eyebrow">NOTTA / LET'S CREATE TOGETHER</p>
      <h1>やってみたい。<br>でも、手が回らない。<br><em>その一歩を、みんなで。</em></h1>
      <p>まだ、ふわっとしたアイデアで大丈夫。<br>10問に答えると、ノッタの運営が企画の概要とファンにお願いできるタスクを整理してお届けします。</p>
      <form id="goal-form" class="goal-form"><label for="goal">どんな企画を、誰とつくりたいですか？</label>
      <textarea id="goal" name="goal" maxlength="1000" required placeholder="例：いつも動画を見てくれるファンと、小さな写真展をつくってみたい。">${esc(intent())}</textarea>
      <p class="form-error" role="alert"></p><button class="button" type="submit">無料でヒアリングをはじめる <span aria-hidden="true">→</span></button>
      <p class="hint">メール認証のあと、1問ずつお聞きします。未定でも大丈夫。<br>初回は1人1企画・無料・カード登録不要</p></form>
      ${state.me?.intake ? '<p class="hint"><a href="/intakes/' + esc(state.me.intake.id) + '">受付済みの企画の続きを見る →</a></p>' : ''}</div>
      <section class="intro-video" aria-labelledby="intro-video-title"><h2 id="intro-video-title">動画で見る、ノッタの使い方</h2><video controls playsinline preload="none" poster="/assets/notta-intro-poster.jpg" width="1920" height="1080" aria-label="ノッタ紹介動画"><source src="/assets/notta-intro.mp4" type="video/mp4">動画を再生できない場合は、<a href="/assets/notta-intro.mp4">紹介動画を開く</a>をご利用ください。</video></section>
      <aside class="example-board case-board" aria-labelledby="cases-title"><div class="board-caption"><h2 id="cases-title">過去事例</h2></div><p class="hint">みんなの力を持ち寄る、4つのモデルケース。企画と人数は架空の設定です。</p><div class="case-grid">${cases.map((c,i) => `<button type="button" class="case-card" data-case="${i}" aria-haspopup="dialog"><span class="sample-label">モデルケース</span><strong>${esc(c.title)}</strong><span>参加者 <b>${c.total}</b> 名</span><span class="case-link">内訳を見る →</span></button>`).join('')}</div></aside></section>
      <div class="intro-strip"><span><b>01</b> 思いついたことをそのまま入力</span><span><b>02</b> 10問にひとつずつ回答</span><span><b>03</b> 数時間後、メールでお知らせ</span></div>
      <section class="section" id="how"><div class="section-head"><h2>「何からやれば？」を、<br>小さなタスクに。</h2><p>初期版では、ヒアリングをAIが担当し、タスク分解は運営が行います。</p></div>
      <div class="steps"><article class="step"><p class="step-num">01</p><h3>やりたいことを話す</h3><p>メール認証でログインしたら、チャットが開きます。目的や日程、手伝ってほしいことなどを1問ずつお聞きします。</p></article>
      <article class="step"><p class="step-num">02</p><h3>ノッタが整理する</h3><p>回答を送信したら、いったんお任せください。運営が企画の概要をまとめ、企画に必要な作業をファンにお願いしやすいタスクに分けます。数時間ほどお時間をいただきます。</p></article>
      <article class="step"><p class="step-num">03</p><h3>できたら、メールで確認</h3><p>メールにあるリンクからログインすると、企画とタスクカードを確認できます。内容のご相談はメールで受け付けます。</p></article></div></section>
      <section class="pricing" id="pricing"><div class="section"><div class="section-head"><h2>まずは、ひとつの企画を無料で。</h2><p>10問のヒアリング、企画の概要、タスク分解の確認まで無料です。自動で課金されることはありません。</p></div>
      <div class="panel"><h3 class="price">0<span>円／初回1企画</span></h3><p>カード登録は不要です。現在、新しい月額プランの申込みは受け付けていません。</p><p class="hint">ファンへの募集公開や担当の割り当ては、この受付には含まれません。企画の実現や参加人数を保証するものではありません。</p><a class="button secondary" href="#goal">自分の企画ではじめる →</a></div></div></section>
      <section class="section"><h2>はじめる前に。</h2><details class="faq"><summary>何も決まっていなくても使えますか？</summary><p>はい。「未定」「おまかせ」でも回答できます。最後の10問目は自由記入で、空欄のまま送信できます。</p></details>
      <details class="faq"><summary>途中で閉じても大丈夫ですか？</summary><p>送信した回答は保存されます。同じメールアドレスでログインすると、続きから回答できます。まだ送信していない入力内容は、同じブラウザーのタブに一時保存します。</p></details>
      <details class="faq"><summary>企画は公開されますか？</summary><p>このヒアリングと完成した結果を確認できるのは、ご本人とノッタの運営だけです。ファンに公開する機能は、今後整えていきます。</p></details></section>`);
    document.querySelectorAll('[data-case]').forEach(button => button.addEventListener('click', () => openCase(Number(button.dataset.case), button)));
    $('#goal-form').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget, goal = form.elements.goal.value.trim();
      intent(goal);
      busy($('button', form), async () => {
        if (!state.me) return auth(() => start(goal));
        await start(goal);
      }, $('.form-error', form), state.me ? '最初の質問を用意しています' : undefined);
    });
  }

  async function start(goal) {
    return nottaWait.run('最初の質問を用意しています', async () => {
      const result = await api('/intakes', {goal});
      clearIntent();
      navigate('/intakes/' + result.id);
    });
  }

  function login(path) {
    main('<div class="error-page"><p class="eyebrow">YOUR PROJECT</p><h1>企画の続きを確認する</h1><p>ヒアリングに使ったメールアドレスでログインしてください。</p><button class="button" id="intake-login">メールでログイン</button></div>');
    $('#intake-login').addEventListener('click', () => auth(() => render(location.pathname)));
  }

  function history(item) {
    return `<details class="intake-history"><summary>最初に入力した企画とこれまでの回答を見る</summary><p class="text-block">${esc(item.goal)}</p>${item.turns.filter(t => t.answer !== null).map(t => `<section><h3>${t.number}. ${esc(t.question)}</h3><p class="text-block">${esc(t.answer || '（記入なし）')}</p></section>`).join('')}</details>`;
  }

  function interview(item) {
    const turn = item.turns.find(t => t.number === item.step), key = draftKey(item);
    const saved = readLocal(key);
    document.title = 'ヒアリング ' + item.step + '/10｜ノッタ';
    main(`<div class="workspace intake-workspace"><div class="workspace-top"><div><p class="eyebrow">YOUR IDEA, ONE STEP AT A TIME</p><h1>やりたいことを聞かせてください。</h1></div>${accountTools()}</div>
      <div class="intake-progress"><span>質問 ${item.step} / 10</span><progress max="10" value="${item.step - 1}" aria-label="回答済みの質問">${item.step - 1} / 10</progress></div>
      <section class="panel intake-chat"><p class="chat-role">ノッタ · ${esc(turn.theme)}</p><h2 id="current-question" tabindex="-1">${esc(turn.question)}</h2>
      <p class="hint">${item.step === 10 ? '空欄でも送信できます。送信後は運営がタスクを整理します。' : '「未定」「おまかせ」でも大丈夫です。答えられる範囲で教えてください。'}</p>
      <form id="answer-form" class="form-stack"><label for="intake-answer">${item.step === 10 ? 'その他の要望（任意）' : 'あなたの回答'}</label>
      <textarea id="intake-answer" name="answer" maxlength="2000" rows="5" ${item.step === 10 ? '' : 'required'}>${esc(saved?.value || '')}</textarea>
      ${item.step < 10 ? '<div class="toolbar"><button class="button secondary compact" type="button" data-quick="未定">未定と答える</button><button class="button secondary compact" type="button" data-quick="おまかせ">おまかせと答える</button></div>' : ''}
      <p class="form-error" role="alert"></p><p id="answer-status" class="hint" role="status"></p>
      <button class="button" type="submit">${item.step === 10 ? '回答を送信してタスク分解を依頼する' : '回答を保存して次へ →'}</button></form>
      <button class="text-button" id="refresh-intake">回答の保存状況を確認する</button><p class="privacy-note">回答はご本人と運営だけが確認できます。秘密情報や不要な個人情報は入力しないでください。</p></section>${history(item)}</div>`);
    const form = $('#answer-form'), field = form.elements.answer;
    field.addEventListener('input', () => { state.dirty = true; writeLocal(key, {value:field.value}); });
    $$('[data-quick]').forEach(button => button.addEventListener('click', () => {
      field.value = button.dataset.quick; field.dispatchEvent(new Event('input')); field.focus();
    }));
    $('#refresh-intake').addEventListener('click', () => busy($('#refresh-intake'), async () => {
      writeLocal(key, {value:field.value, requestId:readLocal(key)?.requestId});
      await render(location.pathname);
    }, null, '保存した内容を確認しています'));
    form.addEventListener('submit', event => {
      event.preventDefault();
      busy($('button[type=submit]', form), async () => {
        const value = field.value.trim(), prior = readLocal(key);
        const requestId = prior?.requestId && prior.value === value ? prior.requestId : crypto.randomUUID();
        writeLocal(key, {value, requestId});
        $('#answer-status').textContent = item.step === 10 ? '回答を保存し、運営に送信しています…' : '回答を保存し、次の質問を用意しています…';
        state.busy = true;
        try {
          const result = await api('/intakes/' + item.id + '/answers', {step:item.step, answer:value, version:item.version, questionHash:turn.questionHash, requestId});
          clearLocal(key); state.dirty = false; show(result);
        } catch (error) {
          if ($('#answer-status')) $('#answer-status').textContent = '';
          if (error.status === 401 || error.status === 403) {
            auth(async () => { state.dirty = false; await render(location.pathname); });
          }
          if (error.status === 409) {
            error.message += ' 「回答の保存状況を確認する」から続きを開けます。';
          }
          throw error;
        } finally { state.busy = false; }
      }, $('.form-error', form), item.step === 10 ? '回答を送信しています' : '回答を保存して、次の質問を用意しています');
    });
    $('#current-question').focus();
  }

  function resultCards(result) {
    return `<section class="panel intake-overview"><p class="eyebrow">YOUR PROJECT</p><h2>${esc(result.title)}</h2><h3>企画の概要</h3><p class="text-block">${esc(result.overview)}</p><h3>最初につくるものと目指すこと</h3><p class="text-block">${esc(result.firstDeliverable)}</p></section>
      <section class="dashboard-section"><h2>みんなにお願いできるタスク</h2><ol class="intake-task-list">${result.tasks.map((task, index) => `<li class="panel"><p class="eyebrow">TASK ${String(index + 1).padStart(2, '0')}</p><h3>${esc(task.title)}</h3><p class="text-block">${esc(task.instructions)}</p><dl><dt>お願いしたい役割</dt><dd>${esc(task.role)}</dd><dt>かかる時間の目安</dt><dd>${esc(task.estimate)}</dd><dt>成果物・完成の条件</dt><dd>${esc(task.completion)}</dd></dl></li>`).join('')}</ol></section>`;
  }

  function show(item) {
    if (item.state === 'interview') return interview(item);
    document.title = (item.result ? '企画とタスク' : 'タスク分解の受付完了') + '｜ノッタ';
    main(`<div class="workspace intake-workspace"><div class="workspace-top"><div><p class="eyebrow">YOUR PROJECT</p><h1>${item.result ? '企画の一歩が見えてきました。' : 'タスク分解の依頼を受け付けました。'}</h1></div>${accountTools()}</div>
      ${item.result ? resultCards(item.result) : `<section class="panel intake-waiting"><span class="pill">${esc(labels[item.state])}</span><h2>${esc(item.message)}</h2><p>この画面は閉じても大丈夫です。完成したら、ご登録のメールアドレスにお知らせします。</p></section>`}
      <section class="intake-receipt"><h2>受付情報</h2><dl><dt>受付ID</dt><dd>${esc(item.id)}</dd><dt>受付日時</dt><dd>${time(item.submitted)}</dd><dt>通知先</dt><dd>${esc(item.email)}</dd>${item.result ? '<dt>結果の更新日時</dt><dd>' + time(item.readyAt) + '</dd>' : ''}</dl>
      <p class="hint">ご相談・修正のご希望：${contact}</p><button class="button secondary compact" id="refresh-intake">最新の状態を確認する</button></section>${history(item)}
      ${legacyLinks()}${state.me?.billing ? billingView(state.me.billing) : ''}</div>`);
    $('#refresh-intake').addEventListener('click', () => busy($('#refresh-intake'), () => render(location.pathname)));
    window.scrollTo(0, 0); headingFocus();
  }

  async function workspace() {
    if (state.me.intake) {
      navigate('/intakes/' + state.me.intake.id); return;
    }
    main(`<div class="workspace intake-workspace"><div class="workspace-top"><h1>${esc(state.me.user.name)}さんのマイページ</h1>${accountTools()}</div><section class="panel"><h2>最初のアイデアからはじめましょう。</h2><p>10問のヒアリングで、やってみたいことを整理します。</p><a class="button" href="/#goal">企画を入力する →</a></section>
      ${legacyLinks()}${billingView(state.me.billing)}</div>`);
  }

  function legacyLinks() {
    const legacy = state.me.legacyProjects || [];
    return `${state.me.projects?.length || state.me.applications?.length ? '<p class="hint"><a href="/workspace?legacy=1">以前の企画・参加記録を見る →</a></p>' : ''}
      ${legacy.length ? '<section class="dashboard-section"><h2>以前の企画</h2>' + legacy.map(p => '<article class="project-card"><h3>' + esc(p.title) + '</h3><p class="text-block">' + esc(p.goal) + '</p></article>').join('') + '</section>' : ''}`;
  }

  function notificationCards(item) {
    return `<section class="panel intake-notifications"><h2>通知の送信記録</h2><p class="hint">「送信サービスで受付済み」は、メール送信サービスが送信依頼を受け付けた状態です。配送の結果はResendで確認してください。定期的な再送は行いません。</p>
      ${item.notifications.map(n => `<div class="intake-notification"><p><strong>${n.kind === 'receipt' ? '運営への受付通知' : '依頼者への完成通知（第' + n.revision + '版）'}</strong><br>${esc(mailLabels[n.status])} · ${time(n.updated)}</p>
      ${n.provider_id ? '<p class="hint">送信ID：' + esc(n.provider_id) + '</p>' : ''}
      ${n.status !== 'sent' ? `<button class="button secondary compact" type="button" data-retry="${n.id}">メール送信を再試行する</button>` : ''}</div>`).join('')}<p id="notification-error" class="form-error" role="alert"></p></section>`;
  }

  async function adminList(before) {
    const data = await api('/admin/intakes' + (before ? '?before=' + encodeURIComponent(before) : ''));
    document.title = 'ヒアリング受付一覧｜ノッタ';
    main(`<div class="workspace"><div class="workspace-top"><div><p class="eyebrow">NOTTA OPERATIONS</p><h1>ヒアリングの受付</h1></div>${accountTools()}</div>
      <p>ヒアリングの回答を確認し、企画とタスクを登録して、依頼者に完成のメールを送れます。</p><div class="project-list">${data.items.map(item => `<article class="project-card"><span class="pill">${esc(labels[item.state])}</span><h2>${esc(item.name)}</h2><p>${esc(item.email)}</p><p class="text-block">${esc(item.goal)}</p><p>受付：${time(item.submitted || item.created)}</p>
      ${item.notifications.some(n => n.status !== 'sent') ? '<p class="banner warning">送信状況の確認が必要な通知があります。</p>' : ''}<a class="button secondary compact" href="/admin/intakes/${item.id}">回答と結果を開く →</a></article>`).join('') || '<p class="empty">まだ受付はありません。</p>'}</div>
      ${data.next ? '<button class="button secondary" id="next-intakes">次の50件</button>' : ''}${before ? '<p><a href="/admin/intakes">一覧の先頭へ</a></p>' : ''}</div>`);
    $('#next-intakes')?.addEventListener('click', () => busy($('#next-intakes'), () => adminList(data.next)));
  }

  function collectResult() {
    const form = $('#result-form');
    if (!form) return adminDraft;
    return {title:$('input[name=title]', form).value, overview:form.elements.overview.value, firstDeliverable:form.elements.firstDeliverable.value,
      tasks:$$('[data-task-index]', form).map(card => Object.fromEntries($$('input,textarea', card).map(field => [field.name, field.value])))};
  }

  function editor(item, value) {
    const tasks = value.tasks || [];
    return `<form id="result-form" class="panel form-stack"><h2>企画とタスクの下書き</h2><p class="hint">下書きは依頼者の画面には表示されません。保存後に「結果を確定して通知」を押すと、保存した結果を依頼者の画面に反映し、メールでお知らせします。</p>
      <label>企画名<input name="title" maxlength="120" value="${esc(value.title || '')}"></label>
      <label>企画の概要<textarea name="overview" rows="5" maxlength="4000">${esc(value.overview || '')}</textarea></label>
      <label>最初につくるものと目指すこと<textarea name="firstDeliverable" rows="3" maxlength="2000">${esc(value.firstDeliverable || '')}</textarea></label>
      <h3>タスクカード</h3><div class="intake-task-editor">${tasks.map((task, index) => `<fieldset class="role-editor" data-task-index="${index}"><legend>タスク ${index + 1}</legend>
      <label>タスク名<input name="title" maxlength="120" value="${esc(task.title)}"></label>
      <label>作業内容<textarea name="instructions" maxlength="3000">${esc(task.instructions)}</textarea></label>
      <label>お願いしたい役割<input name="role" maxlength="120" value="${esc(task.role)}"></label>
      <label>かかる時間の目安<input name="estimate" maxlength="120" value="${esc(task.estimate)}"></label>
      <label>成果物・完了条件<textarea name="completion" maxlength="2000">${esc(task.completion)}</textarea></label>
      <div class="toolbar"><button class="button secondary compact" type="button" data-move="-1" ${index === 0 ? 'disabled' : ''} aria-label="タスク${index + 1}を上に移動">↑ 上へ</button>
      <button class="button secondary compact" type="button" data-move="1" ${index === tasks.length - 1 ? 'disabled' : ''} aria-label="タスク${index + 1}を下に移動">↓ 下へ</button>
      <button class="text-button" type="button" data-remove aria-label="タスク${index + 1}を削除">削除</button></div></fieldset>`).join('')}</div>
      <button class="button secondary compact" type="button" id="add-task" ${tasks.length >= 50 ? 'disabled' : ''}>＋ タスクを追加</button>
      <p class="form-error" role="alert"></p><p class="hint" id="draft-status" role="status">下書き 第${item.draftVersion}版 · 確定済み 第${item.resultVersion}版</p>
      <div class="toolbar"><button class="button secondary" type="submit">下書きを保存</button><button class="button" id="publish-result" type="button" ${item.draftVersion === 0 || item.publishedDraftVersion === item.draftVersion ? 'disabled' : ''}>結果を確定して通知</button></div></form>`;
  }

  function adminDetail(item) {
    adminItem = item;
    adminDraft = structuredClone(item.draft);
    if (!Array.isArray(adminDraft.tasks)) adminDraft.tasks = [emptyTask()];
    document.title = '回答とタスク分解｜ノッタ';
    state.dirty = false;
    main(`<div class="workspace"><p class="breadcrumb"><a href="/admin/intakes">← 受付一覧</a></p><div class="workspace-top"><div><span class="pill">${esc(labels[item.state])}</span><h1>${esc(item.name)}さんの企画</h1><p class="hint">${esc(item.email)} · 受付ID：${item.id}</p></div>${accountTools()}</div>
      <div class="workspace-layout"><aside class="panel"><h2>ヒアリングの回答</h2><p class="text-block">${esc(item.goal)}</p>${item.turns.map(t => `<section class="intake-admin-answer"><h3>${t.number}. ${esc(t.question)}</h3><p class="text-block">${esc(t.answer === null ? '（回答前）' : t.answer || '（記入なし）')}</p></section>`).join('')}</aside>
      <div id="result-editor">${item.state === 'interview' ? '<p class="banner">依頼者の回答が終わるまでお待ちください。</p>' : editor(item, adminDraft)}</div></div>${notificationCards(item)}
      ${item.result ? '<details class="intake-history"><summary>依頼者に表示している結果を見る</summary>' + resultCards(item.result) + '</details>' : ''}</div>`);
    wireEditor();
    $$('[data-retry]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
      if (state.dirty) { notice('先に下書きを保存してください。', true); return; }
      const updated = await api('/admin/intakes/' + item.id + '/retry', {notificationId:button.dataset.retry});
      adminDetail(updated);
    }, $('#notification-error'))));
  }

  function redrawEditor(focusSelector) {
    $('#result-editor').innerHTML = editor(adminItem, adminDraft);
    wireEditor();
    $(focusSelector || '#add-task')?.focus();
    state.dirty = true; $('#publish-result').disabled = true;
    $('#draft-status').textContent = '変更内容はまだ保存されていません。';
  }

  function wireEditor() {
    const form = $('#result-form');
    if (!form) return;
    form.addEventListener('input', () => {
      state.dirty = true; $('#publish-result').disabled = true; $('#draft-status').textContent = '変更内容はまだ保存されていません。';
    });
    $('#add-task').addEventListener('click', () => {
      adminDraft = collectResult(); adminDraft.tasks.push(emptyTask());
      redrawEditor('[data-task-index="' + (adminDraft.tasks.length - 1) + '"] input');
    });
    $$('[data-move]', form).forEach(button => button.addEventListener('click', () => {
      adminDraft = collectResult();
      const index = Number(button.closest('[data-task-index]').dataset.taskIndex), next = index + Number(button.dataset.move);
      [adminDraft.tasks[index], adminDraft.tasks[next]] = [adminDraft.tasks[next], adminDraft.tasks[index]];
      redrawEditor('[data-task-index="' + next + '"] input');
    }));
    $$('[data-remove]', form).forEach(button => button.addEventListener('click', () => {
      adminDraft = collectResult(); adminDraft.tasks.splice(Number(button.closest('[data-task-index]').dataset.taskIndex), 1);
      redrawEditor();
    }));
    form.addEventListener('submit', event => {
      event.preventDefault();
      busy($('button[type=submit]', form), async () => {
        const updated = await api('/admin/intakes/' + adminItem.id + '/result', {draftVersion:adminItem.draftVersion, result:collectResult()});
        adminDetail(updated); notice('下書きを保存しました。依頼者には通知していません。');
      }, $('.form-error', form));
    });
    $('#publish-result').addEventListener('click', () => {
      if (state.dirty) return;
      confirmAction('結果を確定して通知する', '保存した企画とタスクを依頼者の画面に表示し、' + adminItem.email + ' へ完成通知を送ります。', '結果を確定して通知',
        () => api('/admin/intakes/' + adminItem.id + '/complete', {draftVersion:adminItem.draftVersion}));
    });
  }

  async function render(path) {
    if (path === '/') return landing();
    if (!state.me) return login(path);
    if (path === '/workspace' || path === '/billing/return') return workspace();
    const match = path.match(/^\/(admin\/)?intakes\/([a-f0-9]{24})$/);
    try {
      if (path === '/admin/intakes') return await adminList();
      if (match) {
        const item = await api((match[1] ? '/admin' : '') + '/intakes/' + match[2]);
        return match[1] ? adminDetail(item) : show(item);
      }
      main('<div class="error-page"><h1>ページが見つかりません。</h1><a href="/workspace">マイページへ</a></div>');
    } catch (error) {
      if (error.waitTimeout) throw error;
      if (error.status === 401) { state.me = null; return login(path); }
      main('<div class="error-page"><h1>このページを開けませんでした。</h1><p>' + esc(error.message) + '</p><a href="/workspace">マイページへ</a></div>');
    }
  }

  function legal(kind) {
    const company = `<p>MAORA株式会社（マオラ）<br>運営責任者：長谷川峻平<br>東京都千代田区内神田1-9-12 J.NODE 内神田5階<br>${esc(state.config.phone)}<br>${contact}<br><a href="https://www.maora.co.jp/" target="_blank" rel="noopener noreferrer">会社公式サイト ↗</a></p>`;
    const legacy = '<h2>既存の月額契約</h2><p>新規の月額申込みは停止しています。すでに契約している方のお支払い・更新停止・返金の窓口はマイページに残しています。既存契約は月額3,000円（税込総額）で毎月自動更新し、初回支払いから7日以内に申出があれば、初回料金を全額返金します。次回更新の停止はマイページから行えます。通常の更新停止では日割り返金はありません。ご不明な点は運営へお問い合わせください。</p>';
    const pages = {
      terms:['利用規約', '<p>2026年10月27日改定。ノッタはMAORA株式会社（以下「当社」）が提供します。</p><h2>サービスの内容</h2><p>初期版では、最初の企画入力と10問のヒアリングを受け付け、当社が企画の概要と役割ごとのタスクを整理します。AIを利用するのは質問文の作成だけです。AIが利用できない場合は定型の質問で進めます。質問への回答は「未定」「おまかせ」でも構いません。10問目の自由記入は任意です。</p><h2>料金と提供時期</h2><p>初回は1人1企画、ヒアリングからタスク分解の確認まで無料です。カード登録や自動課金はありません。回答を送信すると当社が受け付け、作業完了後にメールで通知します。作業には数時間ほどかかる見込みです。内容や受付状況によって時間がかかる場合があります。</p><h2>閲覧できる人とサービスの提供範囲</h2><p>回答と結果を確認できるのはご本人と運営のみです。この受付にはファンへの募集公開、担当の割当、集客、当日の運営は含みません。企画の実現、人数、収益を保証するものではありません。結果の内容、日程、権利や参加条件はご自身で確認し、ご不明な点は運営へご相談ください。</p><h2>アカウントと投稿</h2><p>18歳以上の方が利用できます。正しいメールアドレスを登録し、確認コードを他人と共有しないでください。投稿の権利は利用者または正当な権利者に帰属します。当社はサービス提供に必要な範囲で保存・処理・表示します。権利侵害、違法行為、なりすまし、嫌がらせ、不正アクセス、過度な利用を禁止します。秘密情報や不要な個人情報は入力しないでください。</p><h2>障害・サービスの変更</h2><p>外部サービスの障害等で機能を一時制限する場合があります。重要な変更や終了は、利用者への案内と必要な対応を含めてお知らせします。当社の故意・重過失など、法令上免責できない責任を免除しません。</p>' + legacy + '<h2>お問い合わせ</h2>' + company],
      privacy:['プライバシーポリシー', '<p>2026年10月29日改定。MAORA株式会社は以下のとおり個人情報を取り扱います。</p><h2>取得する情報と利用目的</h2><p>表示名、確認済みメールアドレス、入力した企画、ヒアリングの質問と回答、作成した結果、認証・送信の記録を、本人認証、タスク分解、完成通知、問い合わせ対応に使用します。認証の不正利用を防ぐため、IPアドレスを秘密鍵で変換した値と試行回数を記録します。広告目的の追跡や営業メールへの転用はしません。既存契約の支払と返金の状態は契約管理に使用します。</p><h2>運営に送信する情報と閲覧できる人</h2><p>すべての回答を送信した時点で、最初に入力した企画、表示名、確認済みメールアドレス、全10問の質問と回答、受付IDと日時を運営の shumpei.hasegawa@maora.co.jp に送信します。企画と結果は本人と運営のみ閲覧でき、この受付で一般公開はしません。作業が完了したら、本人に結果を確認するためのリンクをメールで送ります。閲覧にはログインが必要です。</p><h2>外部サービスへの送信</h2><p>質問文を作成するために、最初に入力した企画とそれまでの質問・回答をOpenAIのAPIまたはGoogle Gemini APIへ送信します。システムから表示名やメールアドレスをAIに送ることはありませんが、回答中に入力した情報は含まれます。検索は使用しません。メール送信にResend、データ保存にNeon、サイト配信にVercelを利用します。国外で処理する場合があります。既存契約の決済はStripeが処理し、当社はカード番号やセキュリティコードを保存しません。</p><h2>保管・安全管理</h2><p>通信の暗号化、本人認証、権限の確認を行います。確認コードは10分で失効し、ハッシュとして保存します。再ログイン後も続きから回答できるよう、送信済みの回答を保存します。まだ送信していない入力内容は同じブラウザーのタブに一時保存します。会計記録など保存義務のある情報を除き、サービスに必要な範囲で保管し、削除・訂正・開示の相談には本人確認のうえ対応します。</p><h2>お問い合わせ</h2>' + company],
      legal:['特定商取引法に基づく表記', company + '<h2>現在の提供内容・価格</h2><p>初回1人1企画。10問のヒアリング、企画の概要とタスク分解の確認まで0円です。回答を受け付けてから数時間を目安に作業し、完成後にメールでお知らせします。インターネット接続の通信費は利用者のご負担です。新規の有料申込みは停止しています。</p>' + legacy + '<h2>お支払い・返金方法（既存契約）</h2><p>Stripeによるカード決済です。申込み時に初回決済、以後毎月同じ日を基準に更新します。同じ日がない月は月末です。返金は原則として元のお支払方法に行い、明細への反映日はカード会社によって異なります。</p><h2>利用環境</h2><p>最新版のChrome・Safari・Edge等とインターネット接続が必要です。</p>'],
    };
    const [title, body] = pages[kind];
    document.title = title + '｜ノッタ';
    main('<article class="legal-page"><p class="breadcrumb"><a href="/">ホーム</a></p><h1>' + title + '</h1>' + body + '</article>');
  }
  return {start, render, legal};
})();

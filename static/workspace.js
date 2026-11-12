'use strict';
window.nottaWorkspace = (() => {
  let current = null, draft = null, tab = 'overview', activeApplication = '', thread = null, lastCursor = 0, timer = null;
  let pendingSend = null;
  const tabs = [['overview','企画概要'],['tasks','準備タスク'],['recruit','募集'],['applications','応募・メッセージ'],['artifacts','資料']];
  const icons = {bulb:'<path d="M9 18h6M10 21h4M8 14a6 6 0 1 1 8 0l-1 2H9z"/>',people:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 4v3"/>'};
  const icon = name => '<div class="nw-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + icons[name] + '</svg></div>';
  const taskKeys = [['title','タスク名',120],['instructions','作業内容',3000],['role','お願いしたい役割',120],['estimate','かかる時間の目安',120],['completion','成果物・完了条件',2000]];
  const publicKeys = [['title','公開する企画名',120],['overview','公開する企画の概要',4000],['schedule','日程・開催場所の案内',1500],['conditions','参加条件・権利・参加費など',2000],['recruitment','募集文',3000]];
  const field = (key,label,value,max,multiline=true,attrs='') => `<label>${label}<${multiline ? 'textarea rows="3"' : 'input'} ${attrs} name="${key}" maxlength="${max}" ${multiline ? '' : 'value="' + esc(value) + '"'}>${multiline ? esc(value) + '</textarea>' : ''}</label>`;
  function stop() { clearInterval(timer); timer = null; }
  function dirty() { state.dirty = true; const el = $('#nw-save-state'); if (el) el.textContent = '未保存の変更があります'; }
  function shell(body) { main('<div class="nw">' + body + '</div>'); }
  async function copyText(value) {
    try { await navigator.clipboard.writeText(value); notice('コピーしました。'); }
    catch { const box=document.createElement('textarea'); box.value=value; document.body.append(box); box.select(); const ok=document.execCommand('copy'); box.remove(); notice(ok?'コピーしました。':'コピーできませんでした。表示された内容を選択してコピーしてください。',!ok); }
  }
  function header() {
    return `<p class="nw-path"><a href="/workspace">マイページ</a><span>›</span><span>自分の企画</span></p><div class="nw-top"><div><p class="eyebrow">LET'S CREATE TOGETHER</p><h1>${esc(draft.title)}</h1><span class="nw-tag">初回1企画・無料</span><span class="nw-tag">${current.state === 'published' ? '募集ページ公開中' : current.state === 'archived' ? '完了・閲覧のみ' : '準備中・本人のみ'}</span></div><div class="nw-actions"><button class="button secondary compact" id="nw-history">編集履歴</button><button class="button compact" id="nw-save">変更を保存</button></div></div><p class="nw-save" id="nw-save-state">${state.dirty ? '未保存の変更があります' : '保存済み · 第' + current.version + '版'}</p>`;
  }
  function overview() {
    return `<section class="nw-card">${icon('bulb')}<h2>やりたいことを、仲間と動く企画に。</h2><p class="hint">運営から届いた企画を、自分の言葉に整えましょう。ここにある内容は、公開操作をするまでご本人と運営だけが確認できます。</p><form id="nw-overview" class="form-stack">${field('title','企画名',draft.title,120,false,'data-project="title"')}${field('overview','企画の概要',draft.overview,4000,true,'data-project="overview"')}${field('firstDeliverable','最初につくるものと目指すこと',draft.firstDeliverable,2000,true,'data-project="firstDeliverable"')}</form></section>`;
  }
  function tasks() {
    const done = draft.tasks.filter(t=>t.status==='完了').length;
    return `<div class="nw-progress"><progress max="${draft.tasks.length}" value="${done}" aria-label="完了したタスク"></progress><span>${done} / ${draft.tasks.length}件が完了</span><button class="button secondary compact" id="nw-add-task" ${draft.tasks.length>=50?'disabled':''}>＋ タスクを追加</button></div><p class="hint">募集する作業は「募集」から選べます。本人用の準備と、協力者にお願いする仕事を分けて進めましょう。</p>${draft.tasks.map((t,i)=>`<article class="nw-card nw-task"><details><summary><span class="nw-number">${String(i+1).padStart(2,'0')}</span><span>${esc(t.title)}</span></summary><div>${taskKeys.map(([k,label,max])=>field(k,label,t[k],max,k!=='title'&&k!=='role'&&k!=='estimate',`data-task="${t.id}" data-key="${k}"`)).join('')}<label>進捗<select data-task="${t.id}" data-key="status">${['未着手','進行中','完了'].map(st=>`<option ${st===t.status?'selected':''}>${st}</option>`).join('')}</select></label><div class="nw-actions"><button class="text-button" data-remove-task="${t.id}" ${current.applications.some(a=>a.task===t.id)?'disabled':''}>このタスクを削除</button><button class="text-button" data-ai-task="${t.id}">この作業をAIに相談</button></div></div></details><p class="nw-task-meta">${esc(t.role || '役割は相談して決定')} · ${esc(t.estimate || '時間は未定')} · ${esc(t.status)}${t.recruit?' · 募集候補':''}</p></article>`).join('')}`;
  }
  function recruit() {
    return `<section class="nw-card"><h2>タスクと役割を、募集ページに。</h2><p class="hint">公開する内容だけを入力してください。下書きを保存しても、すでに公開したページは変わりません。</p>${publicKeys.map(([k,label,max])=>field(k,label,draft.public[k],max,k!=='title',`data-public="${k}"`)).join('')}</section><section class="nw-card"><h2>募集する役割を選ぶ</h2><p class="hint">協力者の応募は無料です。イベント参加費、実費、謝礼などとの関係は役割ごとに確認してください。</p>${draft.tasks.map(t=>`<div class="nw-card"><label class="check-label"><input type="checkbox" data-recruit="${t.id}" ${t.recruit?'checked':''}><span>${esc(t.title)}</span></label>${t.recruit?`<div>${taskKeys.map(([k,label,max])=>field(k,label,t.offer[k],max,k!=='title'&&k!=='role'&&k!=='estimate',`data-offer="${t.id}" data-key="${k}"`)).join('')}${field('terms','参加費・実費・謝礼・権利などの条件',t.offer.terms,2000,true,`data-offer="${t.id}" data-key="terms"`)}<label>募集人数<input type="number" min="1" max="50" value="${t.capacity}" data-capacity="${t.id}"></label><label class="check-label"><input type="checkbox" data-terms="${t.id}" ${t.termsConfirmed?'checked':''}><span>この役割の条件を確認しました</span></label>${current.published.roles?.some(r=>r.id===t.id&&!r.closed)?`<button class="button secondary compact" data-close-role="${t.id}">この役割の受付を停止</button>`:''}</div>`:''}</div>`).join('')}</section><div class="nw-actions"><button class="button" id="nw-preview">公開する内容を確認</button>${current.state==='published'?`<button class="button secondary" id="nw-copy-url">公開URLをコピー</button><a class="button secondary" href="/p/${current.slug}" target="_blank" rel="noopener">募集ページを見る ↗</a>`:''}<button class="button secondary" id="nw-copy-post">告知文をコピー</button></div>`;
  }
  function artifacts() {
    return `<div class="nw-actions"><h2>準備に使う資料</h2><button class="button secondary compact" id="nw-add-artifact">＋ 資料を追加</button></div><p class="hint">AIに「当日の進行表をつくって」「買い物リストにして」と頼むと、ここに保存されます。資料は本人用です。</p>${draft.artifacts.length?draft.artifacts.map(a=>`<section class="nw-card">${field('title','資料名',a.title,120,false,`data-artifact="${a.id}" data-key="title"`)}${field('body','内容',a.body,12000,true,`data-artifact="${a.id}" data-key="body"`)}<div class="nw-actions"><button class="button secondary compact" data-copy-artifact="${a.id}">内容をコピー</button><button class="text-button" data-remove-artifact="${a.id}">削除</button></div></section>`).join(''):'<div class="nw-empty">進行表、募集文、持ち物リストなどを保存できます。</div>'}`;
  }
  function inbox(apps) {
    if (!apps.length) return '<div class="nw-empty">応募が届くと、ここでファンと相談して担当を決められます。募集ページのURLをご自身のSNSなどで案内してください。</div>';
    return `<div class="nw-inbox"><div class="nw-people" aria-label="応募者一覧">${apps.map(a=>`<button data-thread="${a.id}" aria-current="${activeApplication===a.id}">${esc(a.name||a.title||a.projectTitle)} ${a.unread?`<span class="nw-unread">${a.unread}</span>`:''}<small>${esc(a.status)} · ${esc(a.taskTitle||draft?.tasks.find(t=>t.id===a.task)?.title||'')}</small></button>`).join('')}</div><div id="nw-thread"><div class="nw-empty">応募者を選んで、応募内容とメッセージを確認してください。</div></div></div>`;
  }
  function aiBox() {
    return `<aside class="nw-ai nw-card"><details class="nw-ai-details" ${innerWidth>=981?'open':''}><summary>AIと一緒に整える</summary><div><p class="hint">修正や準備資料づくりを相談できます。公開・送信・担当決定はご自身で行います。</p><form id="nw-ai-form"><label for="nw-prompt">お願いしたいこと</label><textarea id="nw-prompt" name="prompt" maxlength="1500" required placeholder="例：当日の進行表をつくって。料理と歓談の時間を分けたい。"></textarea><p class="nw-error" role="alert"></p><button class="button" type="submit" ${current.aiQuota.used>=current.aiQuota.limit?'disabled':''}>AIにお願いする</button></form><p class="hint">この企画のAI利用 ${current.aiQuota.used} / ${current.aiQuota.limit}回<br>手編集と共同作業は引き続き使えます。</p><div class="nw-ai-history" aria-label="AIとの相談履歴">${current.messages.slice(-6).map(m=>`<p><strong>${m.role==='user'?'あなた':'ノッタ'}</strong>${esc(m.body)}</p>`).join('')}</div></div></details></aside>`;
  }
  async function load(id, nextTab) {
    stop(); current = await api('/projects/'+id); draft=structuredClone(current.draft); state.dirty=false;
    if(nextTab)tab=nextTab;
    draw();
  }
  function draw() {
    stop();
    document.title=draft.title+'｜ノッタ';
    shell(`${header()}<div class="nw-tabs" role="tablist" aria-label="企画の操作">${tabs.map(([id,label])=>`<button role="tab" id="nw-tab-${id}" aria-controls="nw-panel" aria-selected="${tab===id}" tabindex="${tab===id?0:-1}" data-w-tab="${id}">${label}${id==='applications'&&current.applications.length?' '+current.applications.length:''}</button>`).join('')}</div><div class="nw-grid"><section class="nw-main" role="tabpanel" id="nw-panel" aria-labelledby="nw-tab-${tab}">${tab==='overview'?overview():tab==='tasks'?tasks():tab==='recruit'?recruit():tab==='artifacts'?artifacts():inbox(current.applications)}</section>${current.state==='archived'?'':aiBox()}</div>`);
    if(tab==='recruit'&&current.applications.length) $('.nw-main').insertAdjacentHTML('afterbegin','<p class="banner">応募済みの企画案内と募集条件は固定されています。役割の条件を変える場合は、旧枠の受付を停止して新しいタスクを追加してください。本人用の準備内容は編集できます。</p>');
    if(current.state==='archived') { $$('input,textarea,select,#nw-save,#nw-add-task,#nw-add-artifact', $('.nw')).forEach(x=>x.disabled=true); }
    wire();
    if(tab==='applications'&&activeApplication&&current.applications.some(a=>a.id===activeApplication)) openThread(activeApplication);
  }
  async function save() {
    if(!state.dirty||!current)return;
    current=await api('/projects/'+current.id+'/edit',{version:current.version,draft}); draft=structuredClone(current.draft);state.dirty=false;
    if($('#nw-save-state'))$('#nw-save-state').textContent='保存済み · 第'+current.version+'版';
  }
  function wire() {
    $('#nw-save').addEventListener('click',()=>busy($('#nw-save'),async()=>{await save();notice('変更を保存しました。公開ページには反映していません。');}));
    $('#nw-history').addEventListener('click',()=>busy($('#nw-history'),showHistory));
    $$('[data-w-tab]').forEach((b,i,all)=>{
      b.addEventListener('click',()=>{tab=b.dataset.wTab;draw();$('#nw-tab-'+tab).focus();});
      b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();all[e.key==='Home'?0:e.key==='End'?all.length-1:(i+(e.key==='ArrowLeft'?-1:1)+all.length)%all.length].click();}});
    });
    $$('[data-project],[data-public],[data-task],[data-offer],[data-artifact]').forEach(el=>el.addEventListener('input',()=>{
      const key=el.dataset.key;
      if(el.dataset.project)draft[el.dataset.project]=el.value;
      if(el.dataset.public)draft.public[el.dataset.public]=el.value;
      if(el.dataset.task)draft.tasks.find(t=>t.id===el.dataset.task)[key]=el.value;
      if(el.dataset.offer){const t=draft.tasks.find(t=>t.id===el.dataset.offer);t.offer[key]=el.value;t.termsConfirmed=false;const cb=$(`[data-terms="${t.id}"]`);if(cb)cb.checked=false;}
      if(el.dataset.artifact)draft.artifacts.find(a=>a.id===el.dataset.artifact)[key]=el.value;
      dirty();
    }));
    $('#nw-overview')?.addEventListener('submit',e=>e.preventDefault());
    $$('[data-recruit]').forEach(el=>el.addEventListener('change',()=>{draft.tasks.find(t=>t.id===el.dataset.recruit).recruit=el.checked;dirty();draw();}));
    $$('[data-capacity]').forEach(el=>el.addEventListener('input',()=>{const t=draft.tasks.find(t=>t.id===el.dataset.capacity);t.capacity=Number(el.value);t.termsConfirmed=false;const cb=$(`[data-terms="${t.id}"]`);if(cb)cb.checked=false;dirty();}));
    $$('[data-terms]').forEach(el=>el.addEventListener('change',()=>{draft.tasks.find(t=>t.id===el.dataset.terms).termsConfirmed=el.checked;dirty();}));
    $('#nw-add-task')?.addEventListener('click',()=>{const id=crypto.randomUUID().replaceAll('-','').slice(0,24);draft.tasks.push({id,title:'新しいタスク',instructions:'',role:'',estimate:'',completion:'',status:'未着手',recruit:false,capacity:1,offer:{title:'新しいタスク',instructions:'',role:'',estimate:'',completion:'',terms:''},termsConfirmed:false});dirty();draw();});
    $$('[data-remove-task]').forEach(el=>el.addEventListener('click',()=>{draft.tasks=draft.tasks.filter(t=>t.id!==el.dataset.removeTask);draft.artifacts.forEach(a=>{if(a.taskId===el.dataset.removeTask)a.taskId='';});dirty();draw();}));
    $$('[data-ai-task]').forEach(el=>el.addEventListener('click',()=>{$('.nw-ai-details').open=true;$('#nw-prompt').value='「'+draft.tasks.find(t=>t.id===el.dataset.aiTask).title+'」の進め方を具体化して。';$('#nw-prompt').focus();}));
    $('#nw-ai-form')?.addEventListener('submit',e=>{e.preventDefault();const f=e.currentTarget;busy($('button',f),async()=>{
      const prompt=f.elements.prompt.value.trim();await save();state.busy=true;
      try{const key='notta-workspace-ai-'+current.id;let ticket;try{ticket=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
        if(!ticket||ticket.prompt!==prompt||ticket.version!==current.version)ticket={requestId:crypto.randomUUID(),prompt,version:current.version,applicationId:tab==='applications'?activeApplication:''};
        sessionStorage.setItem(key,JSON.stringify(ticket));const result=await api('/projects/'+current.id+'/assist',ticket);sessionStorage.removeItem(key);await load(current.id);notice(result.reply,!result.applied);
      }finally{state.busy=false;}
    },$('.nw-error',f),'AIが企画を整えています');});
    $('#nw-preview')?.addEventListener('click',()=>busy($('#nw-preview'),async()=>{await save();showPreview();}));
    $('#nw-copy-url')?.addEventListener('click',()=>copyText(location.origin+'/p/'+current.slug));
    $('#nw-copy-post')?.addEventListener('click',()=>{if(current.state!=='published'){notice('公開すると共有用のURLが発行されます。募集文だけをコピーします。');return copyText(draft.public.recruitment);}return copyText(draft.public.recruitment+'\n'+location.origin+'/p/'+current.slug);} );
    $$('[data-close-role]').forEach(el=>el.addEventListener('click',()=>busy(el,async()=>{await save();current=await api('/projects/'+current.id+'/close-role',{version:current.version,taskId:el.dataset.closeRole});draft=structuredClone(current.draft);draw();notice('この役割の応募受付を停止しました。');})));
    $('#nw-add-artifact')?.addEventListener('click',()=>{draft.artifacts.push({id:crypto.randomUUID().replaceAll('-','').slice(0,24),title:'新しい資料',body:'',taskId:''});dirty();draw();});
    $$('[data-copy-artifact]').forEach(el=>el.addEventListener('click',()=>copyText(draft.artifacts.find(a=>a.id===el.dataset.copyArtifact).body)));
    $$('[data-remove-artifact]').forEach(el=>el.addEventListener('click',()=>{draft.artifacts=draft.artifacts.filter(a=>a.id!==el.dataset.removeArtifact);dirty();draw();}));
    wireThreads();
  }
  function previewHtml(p,roles,applyButtons=false) {
    return `<h2>${esc(p.title)}</h2><h3>企画の概要</h3><p>${esc(p.overview)}</p><h3>日程・場所</h3><p>${esc(p.schedule)}</p><h3>参加条件</h3><p>${esc(p.conditions)}</p><h3>募集について</h3><p>${esc(p.recruitment)}</p><h3>一緒につくる役割</h3><div class="nw-offers">${roles.map(r=>`<article class="nw-card nw-offer"><h3>${esc(r.title)}</h3><span class="nw-tag">${esc(r.role)} · ${r.capacity}名${r.closed?' · 受付終了':''}</span><p>${esc(r.instructions)}</p><dl><dt>時間の目安</dt><dd>${esc(r.estimate)}</dd><dt>成果物・完了条件</dt><dd>${esc(r.completion)}</dd><dt>参加条件</dt><dd>${esc(r.terms)}</dd></dl>${applyButtons?`<button class="button secondary" data-w-apply="${r.id}" ${r.closed?'disabled':''}>${r.closed?'受付終了':'この役割に応募する →'}</button>`:''}</article>`).join('')}</div>`;
  }
  function showPreview() {
    const roles=draft.tasks.filter(t=>t.recruit).map(t=>({...t.offer,id:t.id,capacity:t.capacity}));
    const dialog=$('#confirm-dialog');$('#confirm-content').innerHTML=`<div class="nw-preview"><h2 id="confirm-title">公開前の確認</h2><p class="hint">以下の内容が誰でも見られる募集ページに表示されます。ヒアリングの原文や本人用の資料は公開しません。</p>${previewHtml(draft.public,roles)}<form id="nw-publish-form"><label class="check-label"><input type="checkbox" required><span>企画・募集する役割・人数・参加条件を確認し、この内容を公開します。</span></label><p class="nw-error" role="alert"></p><button class="button" type="submit">公開してURLを発行</button></form></div>`;dialog.showModal();
    $('#nw-publish-form').addEventListener('submit',e=>{e.preventDefault();const f=e.currentTarget;busy($('button',f),async()=>{current=await api('/projects/'+current.id+'/publish',{version:current.version,consent:true});draft=structuredClone(current.draft);dialog.close();draw();notice('募集ページを公開しました。URLをコピーして共有できます。');},$('.nw-error',f));});
  }
  async function showHistory() {
    const result=await api('/projects/'+current.id+'/history');const dialog=$('#confirm-dialog');
    $('#confirm-content').innerHTML=`<h2 id="confirm-title">編集履歴</h2><p class="hint">復元しても公開ページは変わりません。応募のあるタスクを消す復元はできません。</p>${result.items.map(h=>`<div class="nw-actions"><span>第${h.version}版 · ${esc(h.reason)} · ${date(h.created)}</span><button class="button secondary compact" data-restore="${h.version}">この版に戻す</button></div>`).join('')}<p class="nw-error" role="alert"></p>`;dialog.showModal();
    $$('[data-restore]',dialog).forEach(b=>b.addEventListener('click',()=>busy(b,async()=>{await save();current=await api('/projects/'+current.id+'/restore',{version:current.version,restoreVersion:Number(b.dataset.restore)});draft=structuredClone(current.draft);state.dirty=false;dialog.close();draw();notice('履歴から復元しました。');},$('.nw-error',dialog))));
  }
  function wireThreads() {$$('[data-thread]').forEach(b=>b.addEventListener('click',()=>busy(b,async()=>{activeApplication=b.dataset.thread;await openThread(activeApplication);$$('[data-thread]').forEach(x=>x.setAttribute('aria-current',String(x.dataset.thread===activeApplication)));})));}
  function bubbles(messages) { return messages.map(m=>`<div class="nw-message ${m.mine?'mine':''}">${linkedText(m.body)}<small>${new Date(m.created*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})}</small></div>`).join(''); }
  async function openThread(aid) {
    stop();activeApplication=aid;lastCursor=0;thread=await api('/applications/'+aid+'/messages');
    const host=$('#nw-thread');if(!host)return;
    host.innerHTML=`<section class="nw-thread"><div class="nw-thread-head"><h3>${esc(thread.owner?thread.name:thread.projectTitle)}</h3><p>${esc(thread.taskTitle)} · <span id="nw-thread-status">${esc(thread.status)}</span></p><details><summary>応募した内容を見る</summary><p class="text-block">${esc(thread.experience)}</p>${thread.submission?'<p>提出した成果</p><p class="text-block">'+linkedText(thread.submission)+'</p>':''}${thread.feedback?'<p class="text-block">'+esc(thread.feedback)+'</p>':''}</details></div><div class="nw-messages" id="nw-messages" role="log" aria-label="メッセージ" aria-live="polite">${bubbles(thread.messages)}</div><div class="nw-assignment" id="nw-assignment"></div><form class="nw-composer" id="nw-message-form"><label for="nw-message">メッセージ</label><textarea id="nw-message" name="body" maxlength="2000" required placeholder="できることや日程を相談しましょう。"></textarea><p class="nw-error" role="alert"></p><div class="nw-actions"><button class="button compact" type="submit" ${thread.canSend?'':'disabled'}>送信</button>${thread.owner?'<button class="button secondary compact" type="button" id="nw-reply-draft">返信案をAIに相談</button>':''}</div></form></section>`;
    lastCursor=thread.messages.at(-1)?.cursor||0;
    while(thread.next){const more=await api('/applications/'+aid+'/messages?after='+lastCursor);$('#nw-messages').insertAdjacentHTML('beforeend',bubbles(more.messages));lastCursor=more.messages.at(-1)?.cursor||lastCursor;thread.next=more.next;}
    const assignment=$('#nw-assignment');
    if(thread.owner&&thread.canSend&&['応募','候補'].includes(thread.status))assignment.innerHTML='<p>メッセージで条件を相談してから、担当を決めましょう。</p><div class="nw-actions"><button class="button compact" data-app-status="担当">この役割をお願いする</button><button class="text-button" data-app-status="見送り">今回は見送る</button></div>';
    else if(thread.owner&&thread.canSend&&thread.status==='提出')assignment.innerHTML='<label>成果へのコメント<textarea id="nw-feedback" maxlength="1000"></textarea></label><button class="button compact" data-app-status="確認済">成果を確認して貢献を記録</button> <button class="text-button" data-app-status="差戻し">修正をお願いする</button>';
    else if(!thread.owner&&thread.canSend&&['担当','差戻し'].includes(thread.status))assignment.innerHTML='<label>成果を提出（文章・URL）<textarea id="nw-submission" maxlength="4000"></textarea></label><button class="button compact" id="nw-submit-output">企画者に提出</button>';
    else assignment.textContent='現在の状態：'+thread.status;
    $$('[data-app-status]').forEach(b=>b.addEventListener('click',()=>busy(b,async()=>{await api('/applications/'+aid+'/status',{version:thread.version,status:b.dataset.appStatus,comment:$('#nw-feedback')?.value||''});if(current){await load(current.id,'applications');}else{await openThread(aid);}notice('応募の状態を更新しました。');})));
    $('#nw-submit-output')?.addEventListener('click',()=>busy($('#nw-submit-output'),async()=>{await api('/applications/'+aid+'/submit',{version:thread.version,body:$('#nw-submission').value});await openThread(aid);notice('成果を提出しました。');}));
    $('#nw-reply-draft')?.addEventListener('click',()=>{const prompt=$('#nw-prompt');if(prompt){$('.nw-ai-details').open=true;prompt.value='選択した応募者のメッセージへの返信案を、資料として作成して。';prompt.focus();}});
    $('#nw-message-form').addEventListener('submit',e=>{e.preventDefault();const form=e.currentTarget;busy($('button[type=submit]',form),async()=>{const body=form.elements.body.value.trim();if(!pendingSend||pendingSend.body!==body||pendingSend.aid!==aid)pendingSend={body,aid,requestId:crypto.randomUUID()};await api('/applications/'+aid+'/messages',{body,requestId:pendingSend.requestId});pendingSend=null;form.elements.body.value='';await poll();},$('.nw-error',form));});
    await acknowledge();const box=$('#nw-messages');if(box)box.scrollTop=box.scrollHeight;
    timer=setInterval(()=>{if(!document.hidden&&!state.busy&&!nottaWait.current&&$('#nw-thread')?.isConnected)poll().catch(()=>{});},10000);
  }
  async function acknowledge() {if(lastCursor&&!document.hidden)await api('/applications/'+activeApplication+'/read',{cursor:lastCursor});}
  async function poll() {
    const aid=activeApplication;const data=await api('/applications/'+aid+'/messages?after='+lastCursor);if(activeApplication!==aid||!$('#nw-messages'))return;
    if(data.messages.length){$('#nw-messages').insertAdjacentHTML('beforeend',bubbles(data.messages));lastCursor=data.messages.at(-1).cursor;$('#nw-messages').scrollTop=$('#nw-messages').scrollHeight;await acknowledge();}
  }
  function dashboard() {
    stop();current=null;draft=null;state.dirty=false;const projects=state.me.workspaceProjects||[],apps=state.me.workspaceApplications||[];
    shell(`<div class="nw-top"><div><p class="eyebrow">YOUR PROJECTS</p><h1>${esc(state.me.user.name)}さんのマイページ</h1><p class="hint">つくりたいことを、仲間とひとつずつ。</p></div><button class="text-button" data-action="logout">ログアウト</button></div><h2>自分の企画</h2><div class="nw-dashboard-cards">${projects.map(p=>`<article class="nw-card">${icon('bulb')}<h3>${esc(p.title)}</h3><span class="nw-tag">初回1企画・無料</span><span class="nw-tag">${p.state==='published'?'募集ページ公開中':p.state==='archived'?'完了':'準備中・非公開'}</span><div class="nw-actions"><a class="button" href="/workspace#project=${p.id}">企画を開く →</a></div></article>`).join('')||'<p class="nw-empty">運営から企画が届くと、ここに表示されます。</p>'}</div><h2>参加している企画</h2><p class="hint">応募内容・メッセージは、ご本人と企画者だけが確認できます。</p>${inbox(apps)}${state.me.operator?'<p class="nw-actions"><a href="/admin/intakes">運営の受付一覧へ</a></p>':''}<p class="nw-actions"><a href="/workspace?legacy=1">以前の企画・契約管理を見る</a></p>`);wireThreads();
  }
  async function render(path) {
    stop();if(!state.me)return false;
    if(/^\/intakes\/[a-f0-9]{24}$/.test(path)) {const item=await api(path);if(item.projectId){navigate('/workspace#project='+item.projectId);return true;}return false;}
    if(path!=='/workspace')return false;
    const id=new URLSearchParams(location.hash.slice(1)).get('project');
    if(id&&(state.me.workspaceProjects||[]).some(p=>p.id===id)){tab=new URLSearchParams(location.hash.slice(1)).get('tab')||'overview';if(!tabs.some(t=>t[0]===tab))tab='overview';await load(id);return true;}
    if(new URLSearchParams(location.search).has('legacy'))return false;
    if(id)return false;
    if(state.me.workspaceProjects?.length||state.me.workspaceApplications?.length){dashboard();return true;}
    return false;
  }
  function publicPage(p) {
    stop();document.body.classList.add('workspace-theme');document.title=p.title+'｜ノッタの募集';state.public=p;
    shell(`<article class="nw-preview"><p class="eyebrow">LET'S CREATE TOGETHER</p><span class="nw-tag">ノッタでの協力応募は無料</span><p class="hint">企画者：${esc(p.creator)}</p>${previewHtml(p,p.roles,true)}<p class="hint">参加費や実費、謝礼などは各役割の条件をご確認ください。ノッタは集客・企画の実現を保証しません。</p></article>`);
    $$('[data-w-apply]').forEach(b=>b.addEventListener('click',()=>{const open=()=>applyForm(p,b.dataset.wApply);if(!state.me)return auth(open);open();}));
  }
  function applyForm(p,tid) {
    const role=p.roles.find(r=>r.id===tid),dialog=$('#confirm-dialog');$('#confirm-content').innerHTML=`<h2 id="confirm-title">${esc(role.role)}に応募</h2><p>${esc(role.title)}</p><p class="text-block">${esc(role.terms)}</p><form id="nw-apply-form"><label>参加したい理由・できること<textarea name="experience" maxlength="2000" required></textarea></label><label class="check-label"><input type="checkbox" required><span>日程・作業内容・参加費や謝礼・権利などの条件を確認しました。</span></label><p class="nw-error" role="alert"></p><button class="button" type="submit">無料で応募する</button></form>`;dialog.showModal();
    $('#nw-apply-form').addEventListener('submit',e=>{e.preventDefault();const f=e.currentTarget;busy($('button',f),async()=>{await api('/projects/'+p.id+'/apply',{task:tid,experience:f.elements.experience.value,consent:true,publicationRevision:p.publicationRevision});dialog.close();navigate('/workspace');},$('.nw-error',f));});
  }
  return {render,publicPage,save};
})();


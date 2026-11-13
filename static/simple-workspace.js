'use strict';
// Source icons and colors: 紹介資料/動画コード/timeline.js and style.css.
window.nottaSimple = (() => {
  const icons = {
    bulb:'<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
    pin:'<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    pen:'<path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20z"/><path d="M13.5 7.5l3 3"/>',
    mail:'<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="M4.5 7l7.5 6 7.5-6"/>',
    flag:'<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    cam:'<path d="M4 8.5h3l1.6-2.5h6.8L17 8.5h3v10H4z"/><circle cx="12" cy="13.2" r="3.2"/>'
  };
  const primary = [['overview','企画'],['tasks','タスク'],['recruit','募集']];
  let ui = {}, ctx, previousRects = null, focusAfter = '', lastPrimary = 'overview';
  const enabled = () => state.config.planningMode === true && state.config.simpleUiMode === true;
  const canEdit = () => !ctx.c.readOnly && ctx.c.state !== 'archived';
  const id = () => crypto.randomUUID().replaceAll('-','').slice(0,24);
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.bulb}</svg>`;
  const taskIcon = t => /会場|場所/.test(t.title)?'pin':/撮影|写真/.test(t.title)?'cam':/告知|画像|デザイン/.test(t.title)?'pen':/案内|連絡|お礼/.test(t.title)?'mail':/当日|設営|片付/.test(t.title)?'flag':'bulb';
  const text = value => `<p class="ns-text">${esc(value || 'まだ決まっていません')}</p>`;
  const field = (label,value,max,attrs,short=false) => `<label>${label}${short?`<input maxlength="${max}" value="${esc(value)}" ${attrs}>`:`<textarea rows="3" maxlength="${max}" ${attrs}>${esc(value)}</textarea>`}</label>`;
  const taskFields = [['title','タスク名',120],['instructions','作業内容',3000],['role','役割',120],['estimate','時間の目安',120],['completion','成果物・完了条件',2000]];
  const offerFields = [...taskFields,['terms','個別の応募条件・参加費・実費・謝礼・権利',2000]];
  const publicFields = [['title','公開する企画名',120],['overview','企画の紹介',4000],['schedule','日程・場所（未定ならその旨）',1500],['conditions','共通の応募条件・参加費・実費・謝礼・権利',2000],['recruitment','SNSにも使える募集文',3000]];

  function stages(d) {
    const list = (d.roadmap?.stages || []).map(s => ({...s,tasks:d.tasks.filter(t=>t.stageId===s.id)}));
    const known = new Set(list.map(s=>s.id)), unplaced = d.tasks.filter(t=>!known.has(t.stageId));
    if(unplaced.length || !list.length) list.push({id:'unplaced',title:'未分類',outcome:'タスクの段階と進め方を確認しましょう。',tasks:unplaced});
    return list;
  }
  function nextTasks(d) {
    const done = nottaPlanning.stateOf(d).done;
    return [...new Map([...d.tasks.filter(t=>t.status==='進行中'),...d.tasks.filter(t=>t.status!=='完了' && (t.dependsOn||[]).every(x=>done.has(x)))].map(t=>[t.id,t])).values()].slice(0,3);
  }
  function remember() {
    try { sessionStorage.setItem(ui.key,JSON.stringify({stage:ui.stage})); } catch {}
  }
  function prepare(context) {
    ctx = context;
    nottaPlanning.ensure(ctx.d);
    const key = 'notta-simple-'+state.me.user.id+'-'+ctx.c.id;
    if(ui.key!==key) {
      let saved={}; try { saved=JSON.parse(sessionStorage.getItem(key)||'{}'); } catch {}
      ui={key,stage:saved.stage||'',panel:null,prompt:'',conflict:false};
      lastPrimary='overview'; previousRects=new Map();
    }
    const list=stages(ctx.d);
    if(!list.some(s=>s.id===ui.stage)) {
      const next=nextTasks(ctx.d)[0];
      ui.stage=(list.find(s=>s.tasks.some(t=>t.id===next?.id)) || list.find(s=>s.tasks.some(t=>t.status!=='完了')) || list.at(-1)).id;
      remember();
    }
    if(primary.some(([key])=>key===ctx.tab)) lastPrimary=ctx.tab;
  }
  function capture() {
    previousRects = new Map($$('[data-motion-task]', $('#main')).map(el=>[el.dataset.motionTask,el.getBoundingClientRect()]));
  }
  function animate(root=document, rects=previousRects) {
    previousRects=null;
    if(!rects || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    $$('[data-motion-task]',root).filter(el=>{const r=el.getBoundingClientRect();return r.bottom>0 && r.top<innerHeight;}).slice(0,8).forEach((el,i)=>{
      if(!el.animate) return;
      const before=rects.get(el.dataset.motionTask), after=el.getBoundingClientRect();
      const transform=before && after.width && after.height?`translate(${before.left-after.left}px,${before.top-after.top}px) scale(${before.width/after.width},${before.height/after.height})`:'translateY(12px) scale(.97)';
      el.animate([{transform,opacity:.35},{transform:'none',opacity:1}],{duration:230,delay:Math.min(i*15,60),easing:'cubic-bezier(.2,.8,.2,1)'});
    });
  }
  function refresh(focus='') { focusAfter=focus;ctx.redraw(); }
  function navigate(tab) {
    capture();ui.panel=null;document.body.classList.remove('ns-panel-open');
    ctx.navigate(tab);focusAfter='';
    const target=primary.some(([key])=>key===tab)?$('#nw-tab-'+tab):$('#ns-view-title');
    target?.focus({preventScroll:true});
  }
  function open(kind,tid='',trigger='') {
    ui.panel={kind,id:tid,trigger:trigger || (tid?`[data-simple-task="${tid}"]`:'[data-simple-panel="'+kind+'"]')};
    refresh('#ns-dialog-title');
  }
  function close() {
    const trigger=ui.panel?.trigger;
    ui.panel=null;document.body.classList.remove('ns-panel-open');refresh(trigger);
  }
  function header() {
    const {c,d,operator}=ctx,unread=c.applications.reduce((n,a)=>n+Number(a.unread||0),0);
    const back=operator?`/admin/intakes/${operator.intake}`:'/workspace';
    return `<header class="ns-head"><div class="ns-title"><a href="${back}" class="ns-back">${operator?'受付詳細':'企画一覧'}へ</a><h1>${esc(d.title)}</h1><span class="ns-public-state">${c.state==='published'?'募集ページ公開中':c.state==='archived'?'閲覧のみ':'準備中・非公開'}</span></div><div class="ns-tools"><button type="button" class="ns-message-button" data-simple-nav="applications" aria-current="${ctx.tab==='applications'?'page':'false'}">${icon('mail')}<span>メッセージ</span><span class="nw-unread" id="ns-unread" ${unread?'':'hidden'}>${unread}</span></button><details class="ns-menu"><summary aria-label="企画のメニュー">メニュー</summary><div><button type="button" data-simple-nav="artifacts">資料</button><button type="button" data-simple-panel="overview">企画の内容${canEdit()?'を編集':''}</button><button type="button" data-simple-panel="roadmap">道筋${canEdit()?'を編集':'を確認'}</button><button type="button" id="nw-history">編集履歴</button></div></details></div></header><div class="ns-savebar"><span id="nw-save-state" role="status">${state.dirty?'未保存の変更があります':'保存済み · 第'+c.version+'版'}</span>${canEdit()?`<button class="button compact" id="nw-save">${operator?'本人の企画に保存':'変更を保存'}</button>`:''}</div><aside class="ns-conflict" id="ns-conflict" ${ui.conflict?'':'hidden'} role="alert">別の画面で更新されました。入力はこの画面に残っています。<div><button type="button" data-simple-copy-draft>入力内容をコピー</button><a href="${esc(location.href)}" target="_blank" rel="noopener">最新を別画面で確認</a></div></aside><nav class="ns-nav" role="tablist" aria-label="企画を進める">${primary.map(([key,label],i)=>`<button type="button" role="tab" id="nw-tab-${key}" aria-controls="nw-panel" aria-selected="${ctx.tab===key}" tabindex="${ctx.tab===key||(!primary.some(([k])=>k===ctx.tab)&&lastPrimary===key)?0:-1}" data-simple-nav="${key}"><small>0${i+1}</small>${label}</button>`).join('')}</nav>`;
  }
  function taskCard(t) {
    return `<button type="button" class="ns-task-card ${t.status==='完了'?'is-done':t.status==='進行中'?'is-active':''}" data-simple-task="${t.id}" data-motion-task="${t.id}"><span class="ns-card-top"><span class="ns-task-icon">${icon(taskIcon(t))}</span><span class="ns-status">${esc(t.status||'未着手')}</span></span><strong>${esc(t.title||'新しいタスク')}</strong><span class="ns-card-foot"><span>${esc(t.estimate||'時間は未定')}</span><span>${t.recruit?'募集対象':t.recruitCandidate?'募集候補':'本人の準備'}</span></span></button>`;
  }
  function overview() {
    const {d,c,operator}=ctx,r=d.roadmap,progress=nottaPlanning.stateOf(d),done=progress.done.size,next=nextTasks(d);
    return `<section class="ns-project-card"><span class="ns-project-icon">${icon('bulb')}</span><p class="ns-eyebrow">目指すゴール</p><h2>${esc(r?.goal||d.firstDeliverable||d.title)}</h2><div class="ns-progress"><progress max="${d.tasks.length||1}" value="${done}" aria-label="タスクの完了数"></progress><strong>${done}<small> / ${d.tasks.length}件完了</small></strong></div><details class="ns-goal-details"><summary>企画の内容・完了の目安</summary><h3>企画の概要</h3>${text(d.overview)}<h3>最初につくるもの</h3>${text(d.firstDeliverable)}<h3>ここまでできたら企画の完了</h3>${text(r?.criteria)}</details>${r?.confirmedAt?'<p class="rp-finished" role="status">本人が企画の完了を確認しました</p>':progress.ready && d.tasks.length && done===d.tasks.length?`<p>すべてのタスクが完了しました。最後の完了条件を確認しましょう。</p>${!operator&&canEdit()?'<button type="button" class="button" id="rp-complete-goal">企画の完了を確認する</button>':''}`:''}</section><section class="ns-section"><div class="ns-section-title"><h2>ゴールまでの道筋</h2><button type="button" class="text-button" data-simple-panel="map">つながりを見る</button></div>${!r?'<p class="hint">段階と到達点は、運営が準備するとここに表示されます。</p>':''}<div class="ns-journey">${stages(d).map((s,i)=>`<button type="button" class="ns-journey-step ${s.id===ui.stage?'is-current':''}" data-simple-stage-open="${s.id}"><span>${s.id==='unplaced'?'未分類':String(i+1).padStart(2,'0')}</span><strong>${esc(s.title||'名前を確認')}</strong><small>${s.tasks.filter(t=>t.status==='完了').length} / ${s.tasks.length}件</small></button>`).join('')}<div class="ns-journey-goal">GOAL<small>企画の完了を確認</small></div></div></section><section class="ns-section"><div class="ns-section-title"><h2>次にやること</h2><button type="button" class="text-button" data-simple-nav="tasks">タスクを見る</button></div><div class="ns-next">${next.map(t=>`<button type="button" data-simple-task="${t.id}" data-motion-task="${t.id}"><span class="ns-task-icon">${icon(taskIcon(t))}</span><span><strong>${esc(t.title)}</strong><small>${t.status==='進行中'?'進行中':'取りかかれる作業'} · ${esc(t.estimate||'時間は未定')}</small></span><span aria-hidden="true">→</span></button>`).join('')||`<p class="hint">${r?.confirmedAt?'企画の完了を確認しました。記録と資料を見返せます。':done===d.tasks.length?'最後の完了条件を確認しましょう。':'前提の作業とタスクの状態を確認しましょう。'}</p>`}</div></section>`;
  }
  function tasks() {
    const list=stages(ctx.d),index=list.findIndex(s=>s.id===ui.stage),stage=list[index];
    return `<div class="ns-stage-controls"><div class="ns-stage-rail" role="tablist" aria-label="タスクの段階">${list.map((s,i)=>`<button type="button" role="tab" id="ns-stage-${s.id}" aria-controls="ns-stage-panel" aria-selected="${s.id===ui.stage}" tabindex="${s.id===ui.stage?0:-1}" data-simple-stage="${s.id}"><small>${s.id==='unplaced'?'未分類':'STEP '+String(i+1).padStart(2,'0')}</small><strong>${esc(s.title||'段階の名前を確認')}</strong><span>${s.tasks.filter(t=>t.status==='完了').length} / ${s.tasks.length}件完了</span></button>`).join('')}</div><div class="ns-stage-pager"><button type="button" data-simple-stage-move="-1" ${index===0?'disabled':''}>← 前の段階</button><span>${index+1} / ${list.length}</span><button type="button" data-simple-stage-move="1" ${index===list.length-1?'disabled':''}>次の段階 →</button></div></div><section id="ns-stage-panel" class="ns-stage-panel" role="tabpanel" aria-labelledby="ns-stage-${stage.id}"><header class="ns-stage-outcome"><p class="ns-eyebrow">ここまで終わると</p><h2>${esc(stage.outcome||'この段階の到達点を確認しましょう')}</h2><div class="ns-section-title"><span>${stage.tasks.length}件のタスク</span>${canEdit()?`<button type="button" class="text-button" data-simple-add ${ctx.d.tasks.length>=50?'disabled':''}>＋ タスクを追加</button>`:''}</div></header><div class="ns-task-grid">${stage.tasks.map(taskCard).join('')||'<p class="nw-empty">この段階のタスクはまだありません。</p>'}</div></section>`;
  }
  function roleCard(t) {
    return `<article class="ns-role-card ${t.recruit?'is-selected':''}" data-motion-task="${t.id}"><label class="ns-role-select"><input type="checkbox" data-recruit="${t.id}" aria-label="${esc(t.offer.title||t.title)}を募集する" ${t.recruit?'checked':''} ${canEdit()?'':'disabled'}><span class="ns-task-icon">${icon(taskIcon(t))}</span><strong>${esc(t.offer.title||t.title)}</strong><small>${esc(t.offer.role||t.role||'役割を確認')}</small><span class="ns-card-foot"><span>${esc(t.offer.estimate||'時間は未定')}</span><span>${t.capacity}名</span></span></label><button type="button" class="ns-role-detail" data-simple-offer="${t.id}">内容・条件${canEdit()?'を確認・編集':'を確認'}</button></article>`;
  }
  function recruit() {
    const {d,c,operator}=ctx,ready=nottaPlanning.recruitment(d),candidates=d.tasks.filter(t=>t.recruitCandidate||t.recruit),other=d.tasks.filter(t=>!t.recruitCandidate&&!t.recruit);
    return `<ol class="ns-recruit-steps" aria-label="募集の流れ"><li ${c.state!=='published'?'aria-current="step"':''}>1 役割を選ぶ</li><li>2 内容を確認</li><li ${c.state==='published'?'aria-current="step"':''}>3 公開・共有</li></ol>${c.state==='published'?`<section class="ns-share"><h2>ファンにURLを共有しましょう</h2><label>公開中のURL<input id="rp-published-url" readonly value="${esc(location.origin+'/p/'+c.slug)}"></label><div class="ns-share-actions"><button type="button" class="button" id="nw-copy-url">URLをコピー</button><button type="button" class="button secondary" id="nw-copy-post">告知文とURLをコピー</button></div><a href="/p/${c.slug}" target="_blank" rel="noopener">公開中の募集ページを見る ↗</a><p class="hint">下書きの変更は、再公開するまで公開ページに反映されません。</p></section>`:''}<div class="ns-section-title"><div><h2>一緒に進めたい役割を選ぶ</h2><p class="hint">運営が用意した候補から、お願いしたいものを選びます。</p></div><button type="button" class="text-button" data-simple-panel="public">紹介文・共通条件</button></div>${c.applications.length||c.protectedTaskIds?.length?'<p class="ns-note">応募のある募集条件は固定されています。変更する場合は受付を止め、新しい募集枠を用意してください。</p>':''}<div class="ns-role-grid">${candidates.map(roleCard).join('')||'<p class="nw-empty">下の「ほかのタスク」から募集する役割を選べます。</p>'}</div>${other.length?`<details class="ns-other-roles"><summary>ほかのタスクから選ぶ（${other.length}件）</summary><div class="ns-role-grid">${other.map(roleCard).join('')}</div></details>`:''}${ready.missing.length?`<details class="ns-missing" id="rp-recruit-missing"><summary>公開前に確認する項目：${ready.missing.length}件</summary>${ready.missing.map(m=>`<button type="button" class="text-button" data-simple-missing="${m.key}" data-task-id="${m.task}">${esc(m.label)}</button>`).join('')}</details>`:'<p class="rp-ready">募集案の入力は揃っています。次に内容を確認します。</p>'}<div class="ns-recruit-footer"><strong>${ready.selected.length}<small>件の役割を選択中</small></strong>${canEdit()?`<button type="button" class="button" id="nw-preview">${operator?'募集下書きを確認':'選択した役割を確認'}</button>`:''}</div>${operator&&c.published.roles?`<details class="ns-public-copy"><summary>現在公開されている内容</summary>${ctx.previewHtml(c.published,c.published.roles)}</details>`:''}`;
  }
  function taskDetail(t,editing) {
    const {d,c,operator}=ctx;
    if(editing) return `${taskFields.map(([k,label,max])=>field(label,t[k],max,`data-task="${t.id}" data-key="${k}"`,['title','role','estimate'].includes(k))).join('')}${statusField(t)}<label>段階<select data-plan-task="${t.id}"><option value="">未分類</option>${(d.roadmap?.stages||[]).map(s=>`<option value="${s.id}" ${s.id===t.stageId?'selected':''}>${esc(s.title)}</option>`).join('')}</select></label><details><summary>前に終える作業を編集（${t.dependsOn.length}件）</summary><div class="rp-dependencies">${d.tasks.filter(x=>x.id!==t.id).map(x=>`<label class="check-label"><input type="checkbox" data-plan-dependency="${t.id}" value="${x.id}" ${t.dependsOn.includes(x.id)?'checked':''}><span>${esc(x.title)}</span></label>`).join('')}</div></details><button type="button" class="text-button" data-remove-task="${t.id}" ${c.applications.some(a=>a.task===t.id)||c.protectedTaskIds?.includes(t.id)||d.tasks.some(x=>x.dependsOn?.includes(t.id))?'disabled':''}>このタスクを削除</button>`;
    return `<div class="ns-detail-meta"><span>${t.recruit?'募集対象':t.recruitCandidate?'募集候補':'本人の準備'}</span><span>${esc(t.estimate||'時間は未定')}</span></div><h3>作業内容</h3>${text(t.instructions)}<h3>成果物・完了条件</h3>${text(t.completion)}<h3>役割</h3>${text(t.role)}${statusField(t)}${nottaPlanning.connections(d,t)}${canEdit()?`<div class="ns-detail-actions"><button type="button" class="button secondary" data-simple-edit="${t.id}">内容を編集する</button><button type="button" class="text-button" data-simple-recruit-task="${t.id}">${t.recruit?'募集する条件を確認':'この役割を募集する'}</button>${!operator?`<button type="button" class="text-button" data-simple-ai-task="${t.id}">この作業をAIに相談</button>`:''}</div>`:''}`;
  }
  function preview(p,roles) {
    return `<h2 class="ns-review-title">${esc(p.title)}</h2><p class="hint">選択した${roles.length}件の役割です。カードを開くと作業内容と条件を確認できます。</p><div class="ns-review-cards">${roles.map(r=>`<article class="ns-review-role" data-motion-task="${r.id}"><span class="ns-task-icon">${icon(taskIcon(r))}</span><h3>${esc(r.title)}</h3><p>${esc(r.role)} · ${r.capacity}名</p><details><summary>作業内容・条件を確認</summary><h4>お願いする作業</h4>${text(r.instructions)}<h4>時間の目安</h4>${text(r.estimate)}<h4>成果物・完了条件</h4>${text(r.completion)}<h4>この役割の参加条件</h4>${text(r.terms)}</details></article>`).join('')}</div><section class="ns-review-project"><h3>企画の概要</h3>${text(p.overview)}<h3>日程・場所</h3>${text(p.schedule)}<h3>参加条件</h3>${text(p.conditions)}<h3>募集について</h3>${text(p.recruitment)}</section>`;
  }
  function statusField(t) {
    return `<label>進捗<select data-task="${t.id}" data-key="status" ${canEdit()?'':'disabled'}>${['未着手','進行中','完了'].map(s=>`<option ${s===t.status?'selected':''}>${s}</option>`).join('')}</select></label>`;
  }
  function offerDetail(t) {
    const frozen=ctx.c.applications.some(a=>a.task===t.id)||ctx.c.protectedTaskIds?.includes(t.id);
    return `${frozen?'<p class="ns-note">応募があるため、この募集枠の条件は変更できません。本人用の準備タスクは引き続き編集できます。</p>':''}<fieldset ${frozen?'disabled':''}>${offerFields.map(([k,label,max])=>field(label,t.offer[k],max,`data-offer="${t.id}" data-key="${k}"`,['title','role','estimate'].includes(k))).join('')}<label class="check-label"><input type="checkbox" data-plan-common="${t.id}" ${t.useCommonTerms?'checked':''}><span>共通の応募条件を使う</span></label>${t.useCommonTerms?`<p class="ns-note">共通条件：${esc(ctx.d.public.conditions||'紹介文・共通条件から入力してください')}</p>`:''}<label>募集人数<input type="number" min="1" max="50" value="${t.capacity}" data-capacity="${t.id}"></label></fieldset>${!ctx.operator&&canEdit()&&ctx.c.published.roles?.some(r=>r.id===t.id&&!r.closed)?`<button type="button" class="button secondary" data-close-role="${t.id}">この役割の受付を停止</button>`:''}`;
  }
  function panel() {
    const p=ui.panel;if(!p) return '';
    const disabled=!canEdit()&&['offer','overview','roadmap','public'].includes(p.kind);
    const t=ctx.d.tasks.find(t=>t.id===p.id);let title='',body='',save=canEdit();
    if(p.kind==='task'||p.kind==='edit') { if(!t){ui.panel=null;return '';}title=t.title;body=taskDetail(t,p.kind==='edit'); }
    else if(p.kind==='offer') { if(!t){ui.panel=null;return '';}title='募集する内容・条件';body=offerDetail(t); }
    else if(p.kind==='overview') { title='企画の内容';body='<p class="ns-note">変更は、募集を再公開するまで公開ページに反映されません。</p>'+field('企画名',ctx.d.title,120,'data-project="title"',true)+field('企画の概要',ctx.d.overview,4000,'data-project="overview"')+field('最初につくるもの',ctx.d.firstDeliverable,2000,'data-project="firstDeliverable"'); }
    else if(p.kind==='roadmap') { title='ゴールと道筋';body=nottaPlanning.editor(ctx.d); }
    else if(p.kind==='public') {title='募集の紹介文・共通条件';body=`${ctx.c.applications.length||ctx.c.protectedTaskIds?.length?'<p class="ns-note">応募がある募集ページの案内と共通条件は固定されています。</p>':''}<fieldset ${ctx.c.applications.length||ctx.c.protectedTaskIds?.length?'disabled':''}>${publicFields.map(([k,label,max])=>field(label,ctx.d.public[k],max,`data-public="${k}"`,k==='title')).join('')}</fieldset>`;}
    else if(p.kind==='map') { title='ゴールまでのつながり';save=false;const template=document.createElement('template');template.innerHTML=nottaPlanning.roadmap(ctx.d,ctx.c,ctx.operator);body=template.content.querySelector('.rp-map-section')?.outerHTML||'<p>段階とタスクのつながりを準備しています。</p>'; }
    else if(p.kind==='ai') { title='AIに相談';body=ctx.aiBox();save=false; }
    return `<dialog class="ns-dialog ${p.kind==='map'?'ns-map-dialog':''}" id="ns-dialog" aria-labelledby="ns-dialog-title"><header><h2 id="ns-dialog-title" tabindex="-1">${esc(title)}</h2><button type="button" class="ns-close" data-simple-close aria-label="詳細を閉じる">閉じる</button></header><div class="ns-dialog-body">${disabled?'<fieldset disabled>':''}${body}${disabled?'</fieldset>':''}<p class="nw-error" id="ns-panel-error" role="alert"></p></div>${save?`<footer><span class="ns-panel-save-state" role="status">${state.dirty?'未保存の変更があります':'保存済み'}</span><button type="button" class="button" data-simple-save>${ctx.operator?'本人の企画に保存':'変更を保存'}</button></footer>`:''}</dialog>`;
  }
  function render(context) {
    prepare(context);
    document.body.classList.add('simple-workspace');
    const auxiliary=!primary.some(([k])=>k===ctx.tab),label=ctx.tab==='applications'?'メッセージ':'資料';
    return `<div class="ns-workspace">${ctx.operator?`<aside class="ns-operator"><strong>${ctx.c.readOnly?'運営の閲覧モード':'運営の編集モード'}</strong><span>対象：${esc(ctx.c.recipient?.name)}（${esc(ctx.c.recipient?.email)}）</span><small>${ctx.c.readOnly?'本人と同じ企画の内容を確認できます。':'保存すると本人の企画に反映されます。公開・送信・担当決定は本人が行います。'}</small></aside>`:''}${header()}<section class="ns-content" id="nw-panel" ${auxiliary?'role="region" aria-labelledby="ns-view-title"':`role="tabpanel" aria-labelledby="nw-tab-${ctx.tab}"`}>${auxiliary?`<div class="ns-section-title"><h2 id="ns-view-title" tabindex="-1">${label}</h2><button type="button" class="text-button" data-simple-nav="${lastPrimary}">企画の作業に戻る</button></div>`:''}${ctx.tab==='overview'?overview():ctx.tab==='tasks'?tasks():ctx.tab==='recruit'?recruit():ctx.tab==='artifacts'?ctx.artifacts():ctx.operator?'<p class="nw-empty">応募者との会話は企画者と当該応募者だけが閲覧できます。</p>':ctx.inbox(ctx.c.applications)}</section>${!ctx.operator&&canEdit()?'<button type="button" class="ns-ai-button" data-simple-panel="ai">'+icon('bulb')+'AIに相談</button>':''}${panel()}</div>`;
  }
  function selectStage(stage,activate=false) {
    if(!stages(ctx.d).some(s=>s.id===stage)) return;
    ui.stage=stage;remember();capture();
    if(activate&&ctx.tab!=='tasks') return navigate('tasks');
    refresh('#ns-stage-'+stage);
  }
  function mount() {
    const aiButton=$('.ns-ai-button');if(aiButton)$('.ns-savebar').prepend(aiButton);
    if(!canEdit()) {
      $$('input:not([type=checkbox]),textarea',$('.ns-workspace')).forEach(el=>el.readOnly=true);
      $$('select,input[type=checkbox],#nw-add-artifact,[data-remove-artifact]',$('.ns-workspace')).forEach(el=>el.disabled=true);
      $$('[data-copy-artifact]').forEach(b=>b.addEventListener('click',()=>ctx.copyText(ctx.d.artifacts.find(a=>a.id===b.dataset.copyArtifact).body)));
    }
    $$('[data-simple-nav]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.simpleNav)));
    const nav=$$('.ns-nav [role=tab]');nav.forEach((b,i)=>b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();nav[e.key==='Home'?0:e.key==='End'?nav.length-1:(i+(e.key==='ArrowLeft'?-1:1)+nav.length)%nav.length].click();}}));
    $$('[data-simple-stage]').forEach(b=>b.addEventListener('click',()=>selectStage(b.dataset.simpleStage)));
    const tabs=$$('[data-simple-stage]');tabs.forEach((b,i)=>b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();tabs[e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length].click();}}));
    $$('[data-simple-stage-open]').forEach(b=>b.addEventListener('click',()=>selectStage(b.dataset.simpleStageOpen,true)));
    $$('[data-simple-stage-move]').forEach(b=>b.addEventListener('click',()=>{const list=stages(ctx.d),i=list.findIndex(s=>s.id===ui.stage),next=list[i+Number(b.dataset.simpleStageMove)];if(next)selectStage(next.id);}));
    $$('[data-simple-task]').forEach(b=>b.addEventListener('click',()=>open('task',b.dataset.simpleTask)));
    $$('[data-simple-offer]').forEach(b=>b.addEventListener('click',()=>open('offer',b.dataset.simpleOffer,`[data-simple-offer="${b.dataset.simpleOffer}"]`)));
    $$('[data-simple-edit]').forEach(b=>b.addEventListener('click',()=>open('edit',b.dataset.simpleEdit)));
    $$('[data-simple-panel]').forEach(b=>b.addEventListener('click',()=>open(b.dataset.simplePanel)));
    $$('[data-plan-common]').forEach(b=>b.addEventListener('change',()=>refresh(`[data-plan-common="${b.dataset.planCommon}"]`)));
    $$('[data-simple-close]').forEach(b=>b.addEventListener('click',close));
    $$('[data-simple-save]').forEach(b=>b.addEventListener('click',()=>busy(b,ctx.save,$('#ns-panel-error'))));
    $$('[data-simple-add]').forEach(b=>b.addEventListener('click',()=>{if(!canEdit()||ctx.d.tasks.length>=50)return;const tid=id();ctx.d.tasks.push({id:tid,stageId:ui.stage==='unplaced'?'':ui.stage,dependsOn:[],title:'新しいタスク',instructions:'',role:'',estimate:'',completion:'',status:'未着手',recruit:false,recruitCandidate:false,capacity:1,offer:{title:'新しいタスク',instructions:'',role:'',estimate:'',completion:'',terms:''},termsConfirmed:false,useCommonTerms:true});ctx.dirty();open('edit',tid,'[data-simple-add]');}));
    $$('[data-simple-recruit-task]').forEach(b=>b.addEventListener('click',()=>{if(!canEdit())return;const t=ctx.d.tasks.find(t=>t.id===b.dataset.simpleRecruitTask);if(!t.recruit){t.recruit=true;ctx.dirty();}capture();ui.panel={kind:'offer',id:t.id,trigger:`[data-simple-offer="${t.id}"]`};ctx.navigate('recruit');}));
    $$('[data-simple-ai-task]').forEach(b=>b.addEventListener('click',()=>{ui.prompt='「'+ctx.d.tasks.find(t=>t.id===b.dataset.simpleAiTask).title+'」の進め方を具体化して。';open('ai');}));
    $$('[data-simple-missing]').forEach(b=>b.addEventListener('click',()=>{let key=b.dataset.simpleMissing,tid=b.dataset.taskId;if(key==='roles')return $('[data-recruit]')?.focus();if(key==='terms'&&ctx.d.tasks.find(t=>t.id===tid)?.useCommonTerms){key='conditions';tid='';}open(tid?'offer':'public',tid,`[data-simple-missing="${b.dataset.simpleMissing}"]`);const f=tid?$(`[data-offer="${tid}"][data-key="${key}"]`):$(`[data-public="${key}"]`);f?.focus();}));
    $('[data-simple-copy-draft]')?.addEventListener('click',()=>ctx.copyText(draftText(ctx.d)));
    const dialog=$('#ns-dialog');
    if(dialog) {
      document.body.classList.add('ns-panel-open');dialog.showModal();
      dialog.addEventListener('close',()=>{if(dialog.isConnected&&ui.panel)close();});
      const editor=$('#rp-editor',dialog);if(editor)editor.open=true;
      const ai=$('.nw-ai-details',dialog);if(ai)ai.open=true;
      const prompt=$('#nw-prompt',dialog);if(prompt){prompt.value=ui.prompt;prompt.addEventListener('input',()=>ui.prompt=prompt.value);}
    } else document.body.classList.remove('ns-panel-open');
    const surface=$('#ns-stage-panel');let touch;
    surface?.addEventListener('touchstart',e=>{if(e.touches.length===1&&!e.target.closest('input,textarea,select,button,a'))touch={x:e.touches[0].clientX,y:e.touches[0].clientY};else touch=null;},{passive:true});
    surface?.addEventListener('touchend',e=>{if(!touch||!e.changedTouches.length)return;const dx=e.changedTouches[0].clientX-touch.x,dy=e.changedTouches[0].clientY-touch.y;touch=null;if(Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy)*1.5)$(`[data-simple-stage-move="${dx<0?1:-1}"]`)?.click();},{passive:true});
    if(focusAfter){$(focusAfter)?.focus({preventScroll:true});focusAfter='';}
    const selected=$('[data-simple-stage][aria-selected=true]');selected?.scrollIntoView({block:'nearest',inline:'nearest'});
    animate($('#main'));
  }
  function dirty() {
    $$('.ns-panel-save-state').forEach(el=>el.textContent='未保存の変更があります');
  }
  function conflict() {ui.conflict=true;const box=$('#ns-conflict');if(box)box.hidden=false;const message=$('#ns-panel-error');if(message)message.textContent='別の画面で更新されました。入力は保持しています。詳細を閉じて「入力内容をコピー」「最新を別画面で確認」を利用してください。';}
  function saved() {ui.conflict=false;}
  function openTask(tid) {const stage=stages(ctx.d).find(s=>s.tasks.some(t=>t.id===tid));if(stage){ui.stage=stage.id;remember();}open('task',tid);}
  function draftText(d) {
    const name=tid=>d.tasks.find(t=>t.id===tid)?.title||'確認が必要な作業';
    return ['企画：'+d.title,d.overview,'最初につくるもの：'+d.firstDeliverable,'ゴール：'+(d.roadmap?.goal||''),'完了条件：'+(d.roadmap?.criteria||''),
      ...(d.roadmap?.stages||[]).map(s=>'段階：'+s.title+'\n到達点：'+s.outcome),
      ...d.tasks.map(t=>['タスク：'+t.title,...taskFields.slice(1).map(([k,label])=>label+'：'+t[k]),
        '段階：'+(d.roadmap?.stages.find(s=>s.id===t.stageId)?.title||'未分類'),'前に終える作業：'+t.dependsOn.map(name).join('、'),
        '進捗：'+t.status,'募集：'+(t.recruit?'対象':'対象外'),'人数：'+t.capacity,'共通条件を使う：'+(t.useCommonTerms?'はい':'いいえ'),
        ...offerFields.map(([k,label])=>'募集の'+label+'：'+(t.offer[k]||''))].join('\n')),
      ...publicFields.map(([k,label])=>label+'：'+d.public[k]),...d.artifacts.map(a=>'資料：'+a.title+'\n'+a.body)].join('\n\n');
  }
  function updateUnread(c) {const badge=$('#ns-unread');if(badge){const n=c.applications.reduce((sum,a)=>sum+Number(a.unread||0),0);badge.textContent=n;badge.hidden=!n;}}
  function cleanup() {document.body.classList.remove('simple-workspace','ns-panel-open');ui.panel=null;}
  document.addEventListener('click',e=>{const menu=$('.ns-menu[open]');if(menu&&!menu.contains(e.target))menu.open=false;});
  document.addEventListener('keydown',e=>{const menu=$('.ns-menu[open]');if(e.key==='Escape'&&menu&&!$('#ns-dialog[open]')){menu.open=false;$('summary',menu).focus();}});
    return {enabled,render,mount,capture,animate,preview,dirty,conflict,saved,openTask,cleanup,updateUnread,clearPrompt:()=>ui.prompt='',openAI:prompt=>{ui.prompt=prompt;open('ai');}};
})();

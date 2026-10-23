/* Advisory mock output only. It never applies or confirms a goal/task. */
function proposalReviewView(){
 const jobs=loop?.agents?.jobs||[];
 return `<section class="card"><h2>担当の提案をレビュー</h2><p>実AI・検索・CLIは未接続です。以下は画面とデータ形式を確かめる模擬検証です。成果の証拠にはなりません。本人が内容を確認し、対話・分解・出典のフォームへ手動で反映します。</p>${jobs.slice(0,20).map(j=>{
 let result={};try{result=JSON.parse(j.result||'{}')}catch{}const p=result.proposal;
 return `<article class="source"><p>ジョブ ${esc(j.id)} / ${esc(j.status)}</p>${['queued','blocked_connector'].includes(j.status)?`<button class="mock-proposal secondary" data-job="${esc(j.id)}">模擬提案を確認（実AIではありません）</button>`:''}${p?`<strong>模擬・本人レビュー待ち</strong><p>${esc(p.body)}</p>${p.items.map(i=>`<p>${esc(i.title)}${i.acceptance?'：'+esc(i.acceptance):''}${i.url?' / '+esc(i.url)+'（未検証）':''}</p>`).join('')}<p>タスク・親・目的の完了状態は変更していません。</p>`:''}</article>`;
 }).join('')||'<p>先に「担当AIの準備・実接続0」で役割を選び、依頼を保存してください。</p>'}</section>`;
}
function bindProposalReview(){
 document.querySelectorAll('.mock-proposal').forEach(b=>b.onclick=guarded(async()=>{await api('projects/'+active+'/agents/mock_result',{job:b.dataset.job});await refresh();say('模擬提案だけを保存しました。実AI/検索/CLIは実行していません。')}));
}

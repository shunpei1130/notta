const {chromium} = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname,'../data/workspace-browser');
const base='http://127.0.0.1:8892';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const errors=[]; const record=[];
 async function context(){const c=await browser.newContext({viewport:{width:1440,height:1000}});const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));return {c,p};}
 async function login(p,email,name){
  await p.goto(base);await p.locator('#account-button').click();
  await p.locator('#auth-form [name=email]').fill(email);await p.locator('#auth-form [name=name]').fill(name);
  await p.locator('#auth-form input[type=checkbox]').check();await p.locator('#auth-form button[type=submit]').click();
  await p.locator('#code-form').waitFor();const inbox=JSON.parse(await fs.readFile(path.join(root,'inbox.json'),'utf8'));assert.equal(inbox.email,email);
  await p.locator('#code-form [name=code]').fill(inbox.code);await p.locator('#code-form button').click();
  await p.locator('#auth-dialog').waitFor({state:'hidden'});await p.locator('#account-button').click();
 }
 try{
  const owner=await context();await login(owner.p,'owner@workspace.example.test','架空の企画者');
  await owner.p.getByRole('link',{name:'企画を開く →'}).click();await owner.p.locator('#nw-save').waitFor();
  await owner.p.screenshot({path:path.join(root,'desktop-overview.png'),fullPage:true});
  await owner.p.getByRole('tab',{name:'準備タスク',exact:true}).click();assert.equal(await owner.p.locator('.nw-task').count(),14);record.push('owner sees 14 tasks');
  await owner.p.getByRole('tab',{name:'募集',exact:true}).click();
  const values={title:'【架空】ファンとつくる写真展',overview:'写真を選んで展示する、小さな共創企画です。',schedule:'12/19（土）・日程は当事者と確認',conditions:'協力は任意。費用や権利は参加前に確認します。',recruitment:'写真や展示が好きな方と一緒につくりたいです。'};
  for(const [k,v] of Object.entries(values))await owner.p.locator(`[data-public="${k}"]`).fill(v);
  const terms=owner.p.locator('[data-offer][data-key=terms]');for(let i=0;i<await terms.count();i++)await terms.nth(i).fill('無償の任意協力。実費は事前に合意し、参加費の負担はありません。');
  const checks=owner.p.locator('[data-terms]');for(let i=0;i<await checks.count();i++)await checks.nth(i).check();
  await owner.p.locator('#nw-preview').click();await owner.p.locator('#nw-publish-form').waitFor();await owner.p.locator('#nw-publish-form input[type=checkbox]').check();await owner.p.locator('#nw-publish-form button').click();
  await owner.p.locator('#nw-copy-url').waitFor();const href=await owner.p.getByRole('link',{name:'募集ページを見る ↗'}).getAttribute('href');record.push('free publish and public URL');
  const pub=await context();await pub.p.goto(base+href);await pub.p.locator('[data-w-apply]').first().waitFor();assert.equal(await pub.p.locator('[data-w-apply]').count(),4);assert.equal(await pub.p.locator('.nw-preview').textContent().then(s=>s.includes('本人用の概要')),false);await pub.p.screenshot({path:path.join(root,'public-desktop.png'),fullPage:true});
  const fan=await context();await login(fan.p,'fan@workspace.example.test','架空のファン');await fan.p.goto(base+href);await fan.p.locator('[data-w-apply]').first().click();await fan.p.locator('#nw-apply-form [name=experience]').fill('会場探しをお手伝いできます。');await fan.p.locator('#nw-apply-form input[type=checkbox]').check();await fan.p.locator('#nw-apply-form button').click();await fan.p.locator('[data-thread]').first().click();await fan.p.locator('#nw-message-form').waitFor();await fan.p.locator('#nw-message').fill('夕方の会場確認もできます。');await fan.p.locator('#nw-message-form button[type=submit]').click();await fan.p.locator('.nw-message').filter({hasText:'夕方の会場確認もできます。'}).waitFor();record.push('verified fan applies and sends a message');
  await owner.p.reload();await owner.p.getByRole('tab',{name:/応募・メッセージ/}).click();await owner.p.locator('[data-thread]').first().click();await owner.p.locator('.nw-message').filter({hasText:'夕方の会場確認もできます。'}).waitFor();await owner.p.locator('#nw-message').fill('ありがとうございます。候補を一緒に確認しましょう。');await owner.p.locator('#nw-message-form button[type=submit]').click();await owner.p.locator('[data-app-status="担当"]').click();await owner.p.locator('#nw-thread-status').filter({hasText:'担当'}).waitFor();record.push('owner replies and assigns without billing');
  await owner.p.screenshot({path:path.join(root,'desktop-messages.png'),fullPage:true});
  await owner.p.locator('#nw-prompt').fill('当日の進行表をつくって');await owner.p.locator('#nw-ai-form button').click();await owner.p.getByText('この企画のAI利用 1 / 30回',{exact:false}).waitFor();await owner.p.getByRole('tab',{name:'資料',exact:true}).click();await owner.p.locator('[data-artifact][data-key=title]').first().waitFor();assert.equal(await owner.p.locator('[data-artifact][data-key=title]').first().inputValue(),'当日の進行表');record.push('AI material saved in app');
  await owner.p.setViewportSize({width:390,height:844});await owner.p.getByRole('tab',{name:'企画概要',exact:true}).click();await owner.p.screenshot({path:path.join(root,'mobile-overview.png'),fullPage:true});
  assert.ok(await owner.p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile viewport does not overflow');
  await owner.p.getByRole('tab',{name:/応募・メッセージ/}).click();await owner.p.locator('#nw-message-form').waitFor();await owner.p.screenshot({path:path.join(root,'mobile-messages.png'),fullPage:true});assert.ok(await owner.p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile thread does not overflow');
  await owner.p.getByRole('tab',{name:'準備タスク',exact:true}).focus();await owner.p.keyboard.press('ArrowRight');assert.equal(await owner.p.getByRole('tab',{name:'募集',exact:true}).getAttribute('aria-selected'),'true');record.push('mobile layout and keyboard tabs');
  await fan.p.reload();await fan.p.locator('[data-thread]').first().click();await fan.p.locator('.nw-message').filter({hasText:'ありがとうございます。候補を一緒に確認しましょう。'}).waitFor();assert.equal(await fan.p.locator('#nw-thread-status').textContent(),'担当');record.push('fan reload retains conversation and assignment');
  const newcomer=await context();await login(newcomer.p,'newcomer@workspace.example.test','架空の新規企画者');await newcomer.p.goto(base);await newcomer.p.locator('#goal').fill('【検証用】ファンと写真展を開きたい');await newcomer.p.locator('#goal-form button[type=submit]').click();await newcomer.p.locator('[data-answer-choice]').first().waitFor();
  const question=await newcomer.p.locator('#current-question').textContent();await newcomer.p.locator('[data-answer-choice]').first().click();const selected=await newcomer.p.locator('#intake-answer').inputValue();assert.ok(selected.length);await newcomer.p.locator('#intake-answer').fill(selected+'。写真を見ながら交流したい。');await newcomer.p.reload();await newcomer.p.locator('[data-answer-choice]').first().waitFor();assert.equal(await newcomer.p.locator('#current-question').textContent(),question);assert.equal(await newcomer.p.locator('#intake-answer').inputValue(),selected+'。写真を見ながら交流したい。');await newcomer.p.locator('#answer-form button[type=submit]').click();await newcomer.p.getByText('質問 2 / 10',{exact:true}).waitFor();await newcomer.p.setViewportSize({width:390,height:844});assert.ok(await newcomer.p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'interview choices fit mobile viewport');record.push('interview choices and free text retain original question and local draft');
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(root,'browser-check.json'),JSON.stringify({passed:true,checks:record,errors},null,2));console.log(JSON.stringify({passed:true,checks:record,errors}));
 }catch(e){await fs.writeFile(path.join(root,'browser-check.json'),JSON.stringify({passed:false,checks:record,error:String(e),errors},null,2));for(const c of browser.contexts())for(const p of c.pages())await p.screenshot({path:path.join(root,'error-'+browser.contexts().indexOf(c)+'.png'),fullPage:true}).catch(()=>{});throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});


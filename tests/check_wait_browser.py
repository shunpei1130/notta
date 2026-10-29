"""Headless UI regression with fictitious, browser-local responses only."""
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'tests' / 'evidence' / 'wait'
MOCK = r"""
window.mock = {logged:false, step:1, started:false, submitted:false, delay:0, verifyDelay:0,
  configDelay:0, fail:0, ignoreAbort:false, calls:[]};
Object.assign(mock,JSON.parse(sessionStorage.getItem('fixture-session')||'{}'));
const id='aaaaaaaaaaaaaaaaaaaaaaaa';
function item() { return {id,state:mock.submitted?'submitted':'interview',step:mock.step,version:mock.step,
  goal:'架空の写真展',email:'fixture@example.test',message:'タスクを分解しています。',result:null,
  turns:Array.from({length:mock.step},(_,i)=>({number:i+1,theme:'企画のこと',question:'どんな写真展にしたいですか？',
    answer:i+1<mock.step?'おまかせ':null,questionHash:'hash'}))}; }
window.fetch = async (url, options={}) => {
  if (!String(url).startsWith('/api/service/')) throw Error('Non-fixture request');
  const path=String(url).slice('/api/service'.length), method=options.method||'GET';
  mock.calls.push({path,method});
  const delay=path==='/config'?mock.configDelay:path==='/auth/verify'?mock.verifyDelay:
    (method==='POST'&&(path==='/intakes'||path.endsWith('/answers'))?mock.delay:0);
  if(delay) await new Promise((resolve,reject)=>{
    setTimeout(resolve,delay);
    if(!mock.ignoreAbort) options.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
  });
  if(mock.fail===-1&&method==='POST') throw new TypeError('Fixture network failure');
  let body={},status=200;
  if(mock.fail&&method==='POST') {status=mock.fail;body={error:'通信を確認してください。'};}
  else if(path==='/config') body={mailReady:true,intakeMode:true};
  else if(path==='/me') {
    if(!mock.logged) {status=401;body={error:'ログインしてください。'};}
    else body={user:{id:'fixture',name:'確認用',email:'fixture@example.test'},csrf:'fixture',
      projects:[],applications:[],legacyProjects:[],billing:{subscription:null},quota:{},
      intake:mock.started?{id,state:mock.submitted?'submitted':'interview'}:null};
  }
  else if(path==='/auth/start') body={challenge:'fixture',delivery:'sent'};
  else if(path==='/auth/verify') {mock.logged=true;body={ok:true};}
  else if(path==='/intakes'&&method==='POST') {mock.started=true;body=item();}
  else if(path.endsWith('/answers')) {if(mock.step===10)mock.submitted=true;else mock.step++;body=item();}
  else if(path==='/intakes/'+id) body=item();
  else throw Error('Unexpected fixture route '+path);
  sessionStorage.setItem('fixture-session',JSON.stringify({logged:mock.logged,step:mock.step,started:mock.started,submitted:mock.submitted}));
  return new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
};
"""


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

    def do_GET(self):
        if self.path == '/' or self.path.startswith(('/intakes/', '/workspace')):
            self.path = '/service.html'
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)

        def page_for(setup='', width=1280):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.clock.install()
            page.add_init_script(MOCK + '\n' + setup)
            page.goto(base)
            return page

        def intake_page(delay, step=1, **options):
            setup = 'Object.assign(mock,' + json.dumps({'logged': True, 'started': True, 'step': step, 'delay': delay, **options}) + ');'
            page = page_for(setup)
            page.goto(base + '/intakes/aaaaaaaaaaaaaaaaaaaaaaaa')
            expect(page.locator('#intake-answer')).to_be_visible()
            page.locator('#intake-answer').fill('写真を展示したい')
            return page

        # Code verification and the first inference share one continuous modal.
        page = page_for('mock.delay=3000;mock.verifyDelay=3000;')
        page.locator('#goal').fill('架空の写真展')
        page.locator('#goal-form button[type=submit]').click()
        page.locator('[name=email]').fill('fixture@example.test')
        page.locator('[name=name]').fill('確認用')
        page.locator('[name=consent]').check()
        page.locator('#auth-form button[type=submit]').click()
        expect(page.locator('[name=code]')).to_be_focused()
        page.locator('[name=code]').fill('123456')
        page.locator('#code-form button').click()
        expect(page.locator('#wait-title')).to_have_text('ログインを確認しています')
        page.keyboard.press('Escape')
        expect(page.locator('#wait-dialog')).to_be_visible()
        page.keyboard.press('Tab')
        assert page.evaluate("document.activeElement.closest('#wait-dialog')!==null")
        page.clock.run_for(3001)
        expect(page.locator('#wait-title')).to_have_text('最初の質問を用意しています')
        page.screenshot(path=str(EVIDENCE / 'login-desktop.png'))
        page.clock.run_for(3001)
        expect(page.locator('#current-question')).to_be_visible()
        expect(page.locator('#wait-dialog')).not_to_be_visible()
        checks.append('login through first question, focus trap and Escape')
        page.close()

        for delay in (200, 3000, 15000):
            page = intake_page(delay)
            page.locator('#answer-form button[type=submit]').click()
            expect(page.locator('#wait-dialog')).to_be_visible()
            # Direct duplicate event also cannot invoke a second request.
            page.locator('#answer-form').dispatch_event('submit')
            if delay == 15000:
                page.clock.run_for(10001)
                expect(page.locator('#wait-detail')).to_contain_text('通常より時間がかかっています')
                page.clock.run_for(5000)
            else:
                page.clock.run_for(delay + 1)
            expect(page.locator('#wait-dialog')).not_to_be_visible()
            expect(page.locator('.intake-progress')).to_contain_text('質問 2 / 10')
            assert page.evaluate("mock.calls.filter(x=>x.path.endsWith('/answers')).length") == 1
            assert page.evaluate("document.activeElement.id") == 'current-question', page.evaluate("document.activeElement.outerHTML")
            checks.append(f'answer delay {delay}ms, no duplicate and focus restored')
            page.close()

        page = intake_page(65000, ignoreAbort=True)
        page.locator('#answer-form button[type=submit]').click()
        page.clock.run_for(60001)
        expect(page.locator('#wait-dialog')).not_to_be_visible()
        expect(page.locator('#wait-recovery')).to_be_visible()
        expect(page.locator('#intake-answer')).to_have_value('写真を展示したい')
        page.clock.run_for(6000)
        expect(page.locator('.intake-progress')).to_contain_text('質問 1 / 10')
        page.locator('#wait-recovery button').click()
        expect(page.locator('.intake-progress')).to_contain_text('質問 2 / 10')
        assert page.evaluate("mock.calls.filter(x=>x.path.endsWith('/answers')).length") == 1
        checks.append('60s timeout, late reply discarded, GET-only recovery')
        page.close()

        for status in (-1, 401, 409, 500):
            page = intake_page(3000, fail=status)
            page.locator('#answer-form button[type=submit]').click()
            page.clock.run_for(3001)
            expect(page.locator('#wait-dialog')).not_to_be_visible()
            expect(page.locator('#intake-answer')).to_have_value('写真を展示したい')
            if status == 401:
                expect(page.locator('#auth-dialog')).to_be_visible()
                expect(page.locator('[name=email]')).to_be_focused()
            assert page.evaluate("mock.calls.filter(x=>x.path.endsWith('/answers')).length") == 1
            checks.append(f'HTTP {status}, input retained, no retry')
            page.close()

        page = intake_page(3000, step=10)
        page.locator('#answer-form button[type=submit]').click()
        expect(page.locator('#wait-title')).to_have_text('回答を送信しています')
        page.clock.run_for(3001)
        expect(page.locator('#wait-dialog')).not_to_be_visible()
        expect(page.locator('.intake-waiting')).to_be_visible()
        checks.append('final submission ends modal and displays receipt')
        page.close()

        page = page_for('mock.configDelay=3000;', width=390)
        expect(page.locator('#wait-dialog')).to_be_visible()
        page.screenshot(path=str(EVIDENCE / 'waiting-mobile.png'))
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('#wait-dialog .wait-spinner').evaluate("e=>getComputedStyle(e).animationName") == 'none'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.clock.run_for(3001)
        expect(page.locator('#goal')).to_be_visible()
        expect(page.locator('#wait-dialog')).not_to_be_visible()
        checks.append('mobile boot, no overflow and reduced motion')
        page.close()
        page = page_for()
        page.evaluate("nottaWait.run('画面を準備しています',()=>nottaWait.navigating('/workspace'))")
        expect(page.locator('#wait-dialog')).to_be_visible()
        page.clock.run_for(60001)
        expect(page.locator('#wait-dialog')).not_to_be_visible()
        expect(page.locator('#wait-recovery')).to_be_visible()
        checks.append('stalled full-page navigation also times out')
        page.close()
        browser.close()
    server.shutdown()
    print(json.dumps({'passed': len(checks), 'checks': checks}, ensure_ascii=True))


if __name__ == '__main__':
    main()

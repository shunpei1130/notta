"""Local UI check with stubbed APIs; no email, AI, or database changes."""
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from check_wait_browser import StaticHandler, MOCK
from playwright.sync_api import sync_playwright, expect

out = Path(__file__).resolve().parent / 'evidence' / 'intro-video'
out.mkdir(parents=True, exist_ok=True)
srv = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    for width in [375, 390, 760, 761, 1024, 1440]:
        page = browser.new_page(viewport={'width': width, 'height': 900})
        page.add_init_script(MOCK)
        page.goto('http://127.0.0.1:' + str(srv.server_port))
        expect(page.locator('#cases-title')).to_be_visible()
        player = page.locator('.intro-video')
        expect(player).to_be_visible()
        box = player.bounding_box()
        cases = page.locator('.case-board').bounding_box()
        copy = page.locator('.hero-copy').bounding_box()
        assert box['y'] + box['height'] <= cases['y']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width > 760:
            assert box['x'] >= copy['x'] + copy['width']
            assert cases['y'] >= copy['y'] + copy['height']
            assert cases['width'] > box['width']
        page.locator('video').evaluate('(v)=>v.play()')
        page.wait_for_function('document.querySelector("video").currentTime > 0.2')
        assert page.locator('video').evaluate('(v)=>v.videoWidth') == 1920
        page.locator('video').evaluate('(v)=>v.pause()')
        page.screenshot(path=str(out / f'layout-{width}.png'), full_page=True)
        checks.append({'width': width, 'videoVisible': True, 'passed': True})
        page.close()
    browser.close()
srv.shutdown()
(out / 'checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
print(json.dumps(checks))

"""Optional real-browser smoke test. Always creates its own temporary database."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SERVER = '''
import sys, signal
from waitress import create_server
from book_roulette import create_app
app = create_app({'DATABASE': sys.argv[1], 'SECRET_KEY': 'temporary-browser-test-only'})
server = create_server(app, host='127.0.0.1', port=0)
print(server.effective_port, flush=True)
def stop(*args):
    server.task_dispatcher.shutdown()
    server.close()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
server.run()
'''


def main():
    with tempfile.TemporaryDirectory(prefix='book-roulette-browser-') as directory:
        process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-u', '-c', SERVER,
                                    str(Path(directory) / 'browser.sqlite3')], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = process.stdout.readline().strip()
            if not port.isdigit():
                raise RuntimeError(process.stderr.read())
            base = 'http://127.0.0.1:' + port
            with sync_playwright() as playwright:
                launch = {'headless': True}
                if os.environ.get('BROWSER_EXECUTABLE'):
                    launch['executable_path'] = os.environ['BROWSER_EXECUTABLE']
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                external = []
                page.on('request', lambda request: external.append(request.url) if not request.url.startswith(base) else None)
                page.goto(base)
                page.get_by_role('button', name='Wypróbuj dane demo').click()
                page.get_by_role('link', name='Zobacz bibliotekę').click()
                assert page.locator('.book-card').count() == 10
                page.get_by_role('button', name='Zaznacz tę stronę').click()
                assert page.locator('[data-selected-count]').inner_text() == '10'
                card = page.locator('.book-card').first
                card.get_by_role('button').click()
                expect(page.locator('[data-selected-count]')).to_have_text('9')
                card.get_by_role('button').click()
                expect(page.locator('[data-selected-count]')).to_have_text('10')
                page.get_by_role('searchbox').fill('OGRÓD')
                page.get_by_role('button', name='Zastosuj').click()
                assert page.locator('.book-card').count() == 1
                page.get_by_role('searchbox').fill('')
                page.get_by_label('Półka', exact=True).select_option('')
                page.get_by_role('button', name='Zastosuj').click()
                assert page.locator('.book-card').count() == 12

                # All screens fit the required viewport widths, including long titles.
                for width in [375, 390, 430, 768, 1440]:
                    page.set_viewport_size({'width': width, 'height': 900})
                    for route in ['/', '/library', '/import', '/wheel', '/history']:
                        page.goto(base + route)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, route)
                print('Responsive views: 375, 390, 430, 768, 1440 px OK')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.goto(base + '/wheel')
                page.get_by_role('button', name='Losuj książkę', exact=False).click()
                page.locator('#result').wait_for(state='visible')
                assert page.locator('#result-title').inner_text()
                # Assert the actual canvas transform and current server snapshot agree.
                def assert_winner():
                    correct = page.evaluate('''() => {
                      const list = [...document.querySelectorAll('#candidate-list li')];
                      const rotation = Number(document.querySelector('#wheel').style.transform.match(/rotate\\(([-\\d.]+)deg\\)/)[1]);
                      const index = WheelMath.pointerIndex(rotation, list.length);
                      return list[index].textContent.startsWith(document.querySelector('#result-title').textContent + ' — ');
                    }''')
                    assert correct
                assert_winner()
                # Exercise normal animation and ignore double taps while it is running.
                page.emulate_media(reduced_motion='no-preference')
                page.locator('#again').click()
                assert page.locator('#result').is_hidden()
                assert page.locator('#spin').is_disabled()
                page.locator('#result').wait_for(state='visible', timeout=10000)
                assert_winner()
                page.get_by_role('link', name='Historia', exact=True).click()
                assert page.locator('.history-list li').count() == 2

                # Real multipart upload with fictional Unicode + HTML-shaped title.
                page.goto(base + '/import')
                page.locator('#csv-file').set_input_files({'name': 'fictional.csv', 'mimeType': 'text/csv',
                    'buffer': 'Book Id,Title,Author,Exclusive Shelf\n123,"<script>Fikcyjna żółć</script>",Autor,to-read\n'.encode()})
                page.get_by_role('button', name='Importuj bibliotekę').click()
                assert 'Dodano: 1' in page.locator('.notice').inner_text()
                page.goto(base + '/library')
                assert page.get_by_role('heading', name='<script>Fikcyjna żółć</script>', exact=True).is_visible()
                assert not external, external
                assert not errors, errors
                print('Browser workflow: demo, selection, Unicode search, normal/reduced animation, pointer, history, CSV/XSS OK')
                # Screenshot contains only synthetic example data, never a private library.
                # Do not include the deliberately hostile test title in the public screenshot.
                page.goto(base + '/wheel')
                page.set_viewport_size({'width': 1440, 'height': 1100})
                if os.environ.get('SCREENSHOT_PATH'):
                    page.screenshot(path=os.environ['SCREENSHOT_PATH'], full_page=True)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()

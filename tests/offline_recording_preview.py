"""Manual isolated UI check: real recorder + synthetic tone, temporary campaign data.

Run with Python and open the printed URL. This never opens a microphone or calls AI.
"""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCRIPT = r'''
<script>
window.addEventListener('error', event => {
  const box = document.getElementById('fixture-errors');
  if (box) box.textContent += event.message + '\n';
});
const toneContexts = [];
Object.defineProperty(navigator, 'mediaDevices', { value: {
  getUserMedia: async () => {
    const ctx = new AudioContext();
    const oscillator = ctx.createOscillator(), destination = ctx.createMediaStreamDestination();
    oscillator.connect(destination); oscillator.start(); await ctx.resume(); toneContexts.push(ctx);
    destination.stream.getTracks()[0].addEventListener('ended', () => ctx.close(), { once: true });
    return destination.stream;
  },
  enumerateDevices: async () => [], addEventListener: () => {},
} });
document.addEventListener('DOMContentLoaded', () => {
  const panel = document.createElement('div');
  panel.style = 'position:fixed;top:0;right:0;z-index:10000;background:white;padding:8px;border:2px solid red;';
  panel.innerHTML = '<strong>Isolated test — synthetic tone only</strong> <button id="fixture-fail">Simulate save outage</button> <button id="fixture-restore">Restore saving</button><div id="fixture-mode">Saving available</div><pre id="fixture-errors"></pre>';
  document.body.append(panel);
  panel.querySelector('#fixture-fail').onclick = async () => {
    await fetch('/fixture/fail', {method:'POST'}); panel.querySelector('#fixture-mode').textContent = 'Saving unavailable';
  };
  panel.querySelector('#fixture-restore').onclick = async () => {
    await fetch('/fixture/restore', {method:'POST'}); panel.querySelector('#fixture-mode').textContent = 'Saving available';
  };
});
</script>
'''


class FixtureHandler(server.Handler):
    fail_uploads = True

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if urlparse(self.path).path in ('/', '/dnd-audio.html'):
            data = (ROOT / 'dnd-audio.html').read_text(encoding='utf-8').replace('<head>', '<head>' + FIXTURE_SCRIPT).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ('/fixture/fail', '/fixture/restore'):
            FixtureHandler.fail_uploads = path.endswith('fail')
            self._send_json(200, {'ok': True})
            return
        if path == '/api/upload' and FixtureHandler.fail_uploads:
            self._read_body()
            self._send_json(503, {'ok': False, 'error': 'Synthetic save outage; audio must remain queued.'})
            return
        super().do_POST()


if __name__ == '__main__':
    fixture_root = ROOT / '.pycache_tmp'
    fixture_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dnd-offline-preview-', dir=fixture_root) as folder:
        server.UPLOADS_DIR = str(Path(folder, 'uploads'))
        server.CAMPAIGNS_DIR = str(Path(folder, 'campaigns'))
        server.WORLDS_DIR = str(Path(folder, 'worlds'))
        server.NOTES_LAB_RUNS_DIR = str(Path(folder, 'notes-lab-runs'))
        server.load_env_file = lambda *args: None
        server.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('External requests disabled in offline fixture.'))
        httpd = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 0), partial(FixtureHandler, directory=str(ROOT)))
        print(f'Isolated preview: http://127.0.0.1:{httpd.server_port}/', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            server.CAPTURE_LEASES.shutdown()

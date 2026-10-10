"""Persistent, isolated hardware-microphone acceptance server; never port 8000.

Run with --prepare to snapshot the current build, then --serve RUN_DIRECTORY.
Use a NEW, unsigned-in browser profile for this server. No synthetic microphone.
Only the copied application is imported; no production data or secrets are copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from functools import partial
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'server.py', 'ai_usage.py', 'live_player_assistant.py', 'recording_storage.py',
    'capture_reliability.py', 'session_events.py', 'session_highlights.py',
    'session_memory.py', 'session_evidence.py', 'session_reconciliation.py',
    'reconciliation_context.py', 'ai_pricing.json', 'dnd-audio.html',
    'favicon.svg', 'table-ready.css', 'table-ready.js', 'capture-reliability.js',
    'recording-storage.js', 'bootstrap.min.css', 'bootstrap.bundle.min.js',
)
PATCH_FILES = (
    'capture-reliability.js', 'capture_reliability.py', 'dnd-audio.html', 'server.py',
    'tests/recording_stability_ui.test.js', 'tests/recording_storage_ui.test.js',
    'tests/test_capture_reliability.py', 'tests/test_capture_reliability_ui.py',
    'tests/test_session_memory_ui.py', 'tests/october9_stabilization_ui.test.js',
    'OCTOBER9_RECORDING_STABILIZATION.md', 'LATEST_RECORDING_FAILURE_INVESTIGATION.md',
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    run = ROOT / '.pycache_tmp' / ('real-mic-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    app = run / 'app'
    app.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, app / name)
    snapshot = run / 'patch-snapshot'
    for name in PATCH_FILES:
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    (run / 'working-tree-status.txt').write_bytes(subprocess.check_output(['git', 'status', '--short'], cwd=ROOT))
    (run / 'stabilization.patch').write_bytes(subprocess.check_output(['git', 'diff', '--', *PATCH_FILES], cwd=ROOT))
    manifest = {
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'sourceHashes': {name: digest(ROOT / name) for name in dict.fromkeys(FILES + PATCH_FILES)},
        'productionPortExcluded': 8000, 'providersBlocked': True,
        'browserRequirement': 'New unsigned-in profile; retain it and this origin until evidence review.',
    }
    (run / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(run, flush=True)


def serve(folder):
    run = Path(folder).resolve()
    if run.parent != (ROOT / '.pycache_tmp').resolve() or not run.name.startswith('real-mic-'):
        raise RuntimeError('Only a prepared real-mic directory inside .pycache_tmp is permitted.')
    app = run / 'app'
    manifest_path = run / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for name in FILES:
        if digest(app / name) != manifest['sourceHashes'][name]:
            raise RuntimeError('Copied build changed: ' + name)
    # Remove inherited provider/config variables without inspecting their values.
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}
    for name in list(os.environ):
        if name.upper() not in keep:
            del os.environ[name]
    os.chdir(app)
    sys.path.insert(0, str(app))
    import server
    if Path(server.__file__).resolve() != app / 'server.py':
        raise RuntimeError('Wrong server module imported.')

    def blocked(*args, **kwargs):
        with (run / 'blocked-external-calls.log').open('a', encoding='utf-8') as output:
            output.write(str(time.time()) + ' outbound request blocked\n')
        raise RuntimeError('External network/AI disabled in isolated microphone acceptance.')

    server.load_env_file = lambda *args, **kwargs: None
    server.urlopen = blocked
    server.get_openai_api_key = lambda: ''
    server.get_deepgram_api_key = lambda: ''
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    for name in ('uploads', 'campaigns', 'worlds', 'notes-lab-runs'):
        (app / name).mkdir(exist_ok=True)
    if not (app / 'campaigns' / 'hardware-acceptance' / 'campaign.json').exists():
        server.write_campaign('hardware-acceptance', server._default_campaign('hardware-acceptance', 'Isolated microphone acceptance'))

    class Handler(server.Handler):
        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            # No third-party assets, script connections or provider calls from the browser.
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; font-src 'self'; frame-src 'none'; form-action 'self'; base-uri 'self'")
            super().end_headers()

        def log_message(self, format, *args):
            # HTTP metadata only, no bodies or conversational content.
            with (run / 'http.log').open('a', encoding='utf-8') as output:
                output.write(self.log_date_time_string() + ' ' + (format % args) + '\n')

        def do_POST(self):
            parsed = urlparse(self.path)
            allowed = {'/api/session/start', '/api/meta', '/api/session/capture/start',
                       '/api/session/capture/heartbeat', '/api/session/capture/event',
                       '/api/upload', '/api/session/finalize', '/api/campaign/save'}
            if parsed.path not in allowed:
                self._read_body()
                self._send_json(403, {'ok': False, 'error': 'Route disabled in deferred microphone acceptance.'})
                return
            if parsed.path == '/api/upload' and parse_qs(parsed.query).get('deferTranscription') != ['1']:
                self._read_body()
                self._send_json(403, {'ok': False, 'error': 'Deferred transcription is required.'})
                return
            if parsed.path == '/api/session/finalize':
                data = json.loads(self._read_body().decode('utf-8'))
                if data.get('deferTranscription') is not True:
                    self._send_json(403, {'ok': False, 'error': 'Deferred finalization is required.'})
                    return
                # Delegate unchanged handler after returning the inspected body.
                body = json.dumps(data).encode('utf-8')
                self._read_body = lambda: body
            super().do_POST()

    port = int(manifest.get('port', 0))
    if port == 8000:
        raise RuntimeError('Production port forbidden.')
    httpd = ThreadingHTTPServer(('127.0.0.1', port), partial(Handler, directory=str(app)))
    if httpd.server_port == 8000:
        httpd.server_close()
        raise RuntimeError('Production port forbidden.')
    manifest.update(port=httpd.server_port, pid=os.getpid(), url=f'http://127.0.0.1:{httpd.server_port}/', startedAt=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'url': manifest['url'], 'pid': os.getpid(), 'persistentDirectory': str(run), 'providersBlocked': True}), flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        server.CAPTURE_LEASES.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--serve', metavar='RUN_DIRECTORY')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        serve(args.serve)

"""Session status fault injection; synthetic folders, no providers or real sessions."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

import server
from tests import test_recording_storage as storage_fixtures
from tests.test_recording_storage import wav_bytes


def sharing_error(code=32):
    error = OSError('synthetic Windows sharing violation')
    error.winerror = code
    return error


class StatusFileStabilityTests(unittest.TestCase):
    setUp = storage_fixtures.OfflineRecordingTests.setUp

    def handler(self, data=None, query='sessionId=1234567890123&chunkIndex=0'):
        data = wav_bytes() if data is None else data
        handler = object.__new__(server.Handler)
        handler.path = '/api/upload?' + query
        handler.headers = Message()
        handler.headers['Content-Type'] = 'audio/wav'
        handler.headers['Content-Length'] = str(len(data))
        handler.rfile = BytesIO(data)
        handler._send_json = mock.Mock()
        return handler

    def test_initialization_cannot_overwrite_a_competing_chunk_update(self):
        # Hold initialization at its first write, while a chunk update requests
        # the same lock. Events control ordering; no sleeps establish correctness.
        initial_write, release, competing_lock = (threading.Event() for _ in range(3))
        real_write, real_lock = server.write_json_atomic, server._status_lock
        def write(path, data, **kwargs):
            if threading.current_thread().name.startswith('init'):
                initial_write.set()
                if not release.wait(5):
                    raise AssertionError('initialization was not released')
            return real_write(path, data, **kwargs)
        def lock(path):
            if threading.current_thread().name.startswith('chunk'):
                competing_lock.set()
            return real_lock(path)
        with mock.patch.object(server, 'write_json_atomic', side_effect=write), mock.patch.object(server, '_status_lock', side_effect=lock):
            with ThreadPoolExecutor(1, thread_name_prefix='init') as initializer, ThreadPoolExecutor(1, thread_name_prefix='chunk') as updater:
                first = initializer.submit(server.init_session, self.sid, 'synthetic')
                try:
                    self.assertTrue(initial_write.wait(5))
                    second = updater.submit(server.update_status_for_chunk, self.sid, 7, 'chunk_0007.wav', 100, 'queued')
                    self.assertTrue(competing_lock.wait(5))
                finally:
                    release.set()
                first.result(5); second.result(5)
        status = server._read_session_status(self.sid)
        self.assertEqual('synthetic', status['campaignId'])
        self.assertEqual(7, status['chunks'][0]['chunkIndex'])
        with mock.patch.object(server, 'write_json_atomic') as writer:
            for _ in range(5):
                server.init_session(self.sid, 'different')
            writer.assert_not_called()

    def test_concurrent_metadata_capture_and_chunks_preserve_each_other(self):
        barrier = threading.Barrier(12)
        def update(index):
            barrier.wait(5)
            server.init_session(self.sid, 'synthetic')
            server.update_status_for_chunk(self.sid, index, f'chunk_{index:04d}.wav', 100, 'queued')
            server._set_session_status_fields(self.sid, {f'metadata{index}': index})
            server._store_tracking_state(self.sid, {'synthetic': True})
        with ThreadPoolExecutor(12) as pool:
            list(pool.map(update, range(12)))
        server._set_session_status_fields(self.sid, {'capture': {'state': 'healthy'}})
        with mock.patch.object(server, 'read_campaign', return_value={}), mock.patch.object(server, '_build_context_snapshot', return_value={'synthetic': True}):
            server._assign_session_campaign(self.sid, 'synthetic')
        status = server._read_session_status(self.sid)
        self.assertEqual(list(range(12)), sorted(c['chunkIndex'] for c in status['chunks']))
        self.assertEqual(list(range(12)), [status[f'metadata{i}'] for i in range(12)])
        self.assertEqual('healthy', status['capture']['state'])
        self.assertEqual({'synthetic': True}, status['trackingState'])

    def test_status_read_retries_sharing_errors_without_substituting_defaults(self):
        folder = Path(server.init_session(self.sid))
        before = (folder / 'status.json').read_bytes()
        real_read = server.read_json
        for error in (PermissionError('access denied'), sharing_error(32), sharing_error(33)):
            with self.subTest(error=error), mock.patch.object(server.time, 'sleep'), mock.patch.object(server, 'read_json', side_effect=[error, json.loads(before)]):
                self.assertEqual(self.sid, server._read_session_status(self.sid)['sessionId'])
        with mock.patch.object(server.time, 'sleep'), mock.patch.object(server, 'read_json', side_effect=PermissionError('persistent denial')) as reader:
            with self.assertRaises(PermissionError):
                server.init_session(self.sid)
            self.assertEqual(6, reader.call_count)
        self.assertEqual(before, (folder / 'status.json').read_bytes())
        self.assertEqual(self.sid, real_read(folder / 'status.json', {})['sessionId'])

    def test_status_replace_retries_atomically_and_exhaustion_preserves_old_file(self):
        folder = Path(server.init_session(self.sid))
        original = (folder / 'status.json').read_bytes()
        real_replace = server.os.replace
        calls = []
        def replace(source, target):
            calls.append(source)
            self.assertEqual(original, Path(target).read_bytes())
            if len(calls) < 3:
                raise sharing_error(33)
            return real_replace(source, target)
        with mock.patch.object(server.time, 'sleep'), mock.patch.object(server.os, 'replace', side_effect=replace):
            server._set_session_status_fields(self.sid, {'sessionName': 'preserved'})
        self.assertEqual(1, len(set(calls)))
        before_failure = (folder / 'status.json').read_bytes()
        with mock.patch.object(server.time, 'sleep'), mock.patch.object(server.os, 'replace', side_effect=PermissionError('persistent denial')):
            with self.assertRaises(PermissionError):
                server._set_session_status_fields(self.sid, {'sessionName': 'must not appear'})
        self.assertEqual(before_failure, (folder / 'status.json').read_bytes())
        self.assertEqual([], list(folder.glob('*.tmp')))

    def test_unrelated_io_errors_and_other_json_reads_are_not_retried(self):
        operation = mock.Mock(side_effect=OSError('disk failure'))
        with mock.patch.object(server.time, 'sleep') as sleep:
            with self.assertRaises(OSError):
                server._retry_status_io(operation)
            operation.assert_called_once(); sleep.assert_not_called()
        with mock.patch('builtins.open', side_effect=PermissionError('denied')) as opener:
            with self.assertRaises(PermissionError):
                server.read_json('unrelated.json', {})
            opener.assert_called_once()

    def test_initialization_failure_occurs_only_after_complete_body_read(self):
        handler = self.handler()
        def fail(_):
            self.assertEqual(len(handler.rfile.getvalue()), handler.rfile.tell())
            raise sharing_error()
        with mock.patch.object(server, 'init_session', side_effect=fail), mock.patch.object(server, 'enqueue_transcription') as provider:
            handler.do_POST()
            provider.assert_not_called()
        self.assertEqual(503, handler._send_json.call_args.args[0])
        self.assertTrue(handler.close_connection)
        retry = self.handler(); retry.do_POST()
        self.assertEqual(200, retry._send_json.call_args.args[0])

    def test_pre_body_header_rejections_close_connection_without_initializing(self):
        for length, query, expected in [('536870913', '', 413), ('-1', '', 400), ('no', '', 400), ('10', 'sessionId=bad&chunkIndex=0', 400)]:
            handler = self.handler(query=query or 'sessionId=1234567890123&chunkIndex=0')
            handler.headers.replace_header('Content-Length', length)
            with mock.patch.object(server, 'init_session') as init:
                handler.do_POST(); init.assert_not_called()
            self.assertEqual(expected, handler._send_json.call_args.args[0])
            self.assertEqual(0, handler.rfile.tell())
            self.assertTrue(handler.close_connection)

    def test_lost_acknowledgment_retry_preserves_one_file_and_status_entry(self):
        handler = self.handler()
        handler._send_json.side_effect = BrokenPipeError('client disconnected')
        with self.assertRaises(BrokenPipeError):
            handler.do_POST()
        before = Path(self.temp.name, self.sid, 'chunk_0000.wav').read_bytes()
        retry = self.handler(); retry.do_POST()
        code, receipt = retry._send_json.call_args.args
        self.assertEqual(200, code); self.assertTrue(receipt['duplicate'])
        self.assertEqual(before, Path(self.temp.name, self.sid, 'chunk_0000.wav').read_bytes())
        self.assertEqual(1, len(server._read_session_status(self.sid)['chunks']))

    def test_body_read_failure_before_processing_is_retryable_without_status_access(self):
        handler = self.handler()
        with mock.patch.object(handler, '_read_body', side_effect=ConnectionResetError('synthetic disconnect')), mock.patch.object(server, 'init_session') as init:
            handler.do_POST(); init.assert_not_called()
        self.assertEqual(503, handler._send_json.call_args.args[0])
        self.assertEqual(0, handler.rfile.tell())
        self.assertTrue(handler.close_connection)

    def test_http_status_failure_then_retry_and_static_page(self):
        class QuietHandler(server.Handler):
            def log_message(self, *_):
                pass
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=server.APP_DIR))
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        def request(method, path, body=None):
            connection = HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
            try:
                connection.request(method, path, body=body, headers={'Content-Type': 'audio/wav'})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        try:
            self.assertEqual(302, request('GET', '/')[0])
            code, body = request('GET', '/dnd-audio.html')
            self.assertEqual(200, code); self.assertIn(b'captureSaveStatus', body)
            route = f'/api/upload?sessionId={self.sid}&chunkIndex=0'
            with mock.patch.object(server, 'init_session', side_effect=sharing_error()):
                self.assertEqual(503, request('POST', route, wav_bytes())[0])
            with mock.patch.object(server, 'enqueue_transcription') as provider:
                self.assertEqual(200, request('POST', route, wav_bytes())[0])
                code, body = request('POST', route, wav_bytes())
                self.assertEqual(200, code); self.assertTrue(json.loads(body)['duplicate'])
                provider.assert_not_called()
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(5)


if __name__ == '__main__':
    unittest.main()

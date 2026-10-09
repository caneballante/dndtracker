from email.message import Message
from io import BytesIO
import hashlib
import json
from pathlib import Path
import struct
from unittest import mock
import unittest

import server
from recording_storage import validate_audio
from tests import test_recording_storage as fixtures
from tests import test_capture_reliability as capture_fixtures


class StorageStabilityTests(unittest.TestCase):
    setUp = fixtures.OfflineRecordingTests.setUp
    def test_webm_requires_complete_block_but_accepts_open_streaming_container(self):
        header = b'\x1a\x45\xdf\xa3\x80' + b'\xec\xe4' + b'x' * 100
        cluster = b'\x18\x53\x80\x67\xff\x1f\x43\xb6\x75\xff'
        block = b'\xa3\x8a\x81\x00\x00\x80' + b'abcdef'
        validate_audio(header + cluster + block, '.webm')
        with self.assertRaises(ValueError):
            validate_audio(header + cluster + block[:-1], '.webm')

    def test_empty_ogg_and_mp4_headers_are_rejected(self):
        for extension, data in [('.ogg', b'OggS' + b'\x00' * 22 + b'\x01\x08OpusHead'),
                                ('.mp4', b'\x00\x00\x00\x0cftypisom')]:
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                validate_audio(data, extension)
        validate_audio(b'\x00\x00\x00\x0cmdatdata', '.mp4')

    def test_restart_resumes_only_explicit_pending_jobs(self):
        server.init_session(self.sid)
        server.update_status_for_chunk(self.sid, 0, 'chunk_0000.wav', 100, 'pending')
        server.update_status_for_chunk(self.sid, 1, 'chunk_0001.wav', 100, 'queued')
        with mock.patch.object(server, 'enqueue_transcription') as dispatch:
            server.resume_transcription_jobs()
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual((self.sid, 0), dispatch.call_args.args[:2])

    def test_dispatch_deduplicates_pending_work_and_persists_intent(self):
        folder = server.init_session(self.sid)
        server.update_status_for_chunk(self.sid, 0, 'chunk_0000.wav', 100, 'pending')
        with mock.patch.object(server, '_TRANSCRIPTION_JOBS', set()), mock.patch.object(server, '_TRANSCRIPTION_POOL') as pool:
            server.enqueue_transcription(self.sid, 0, str(Path(folder, 'chunk_0000.wav')))
            server.enqueue_transcription(self.sid, 0, str(Path(folder, 'chunk_0000.wav')))
            self.assertEqual(1, pool.submit.call_count)
        self.assertTrue(server._read_session_status(self.sid)['chunks'][0]['durableTranscriptionJob'])

    def test_full_diarization_never_silently_omits_compressed_recovery(self):
        folder = Path(server.init_session(self.sid))
        (folder / 'chunk_0000.webm').write_bytes(b'synthetic placeholder')
        with self.assertRaisesRegex(RuntimeError, 'Backfill Missing Transcripts'):
            server.generate_full_diarized_transcript_for_session(self.sid)

    def test_metadata_cannot_conceal_truncated_wav_payload(self):
        fmt = struct.pack('<HHIIHH', 1, 1, 16000, 32000, 2, 16)
        body = b'WAVEfmt ' + struct.pack('<I', 16) + fmt + b'JUNK' + struct.pack('<I', 100) + b'x' * 100
        body += b'data' + struct.pack('<I', 100) + b'\x01\x00'
        with self.assertRaises(ValueError):
            validate_audio(b'RIFF' + struct.pack('<I', len(body)) + body, '.wav')

    def test_session_initialization_merges_with_newly_saved_chunk(self):
        folder = server.init_session(self.sid)
        def context(_):
            server.update_status_for_chunk(self.sid, 0, 'chunk_0000.wav', 32044, 'queued')
            return {}
        body = json.dumps({'sessionId': self.sid, 'campaignId': 'default', 'sessionName': 'Synthetic'}).encode()
        handler = object.__new__(server.Handler)
        handler.path = '/api/session/start'; handler.headers = Message()
        handler.headers['Content-Length'] = str(len(body)); handler.rfile = BytesIO(body)
        handler._send_json = mock.Mock()
        with mock.patch.object(server, 'read_campaign', return_value={}), mock.patch.object(server, '_build_context_snapshot', side_effect=context):
            handler.do_POST()
        self.assertEqual(200, handler._send_json.call_args.args[0])
        status = json.loads(Path(folder, 'status.json').read_text())
        self.assertEqual(1, len(status['chunks']))
        self.assertEqual('Synthetic', status['sessionName'])

    def test_source_digest_handles_representation_change_after_lost_receipt(self):
        blob = fixtures.wav_bytes()
        digest = hashlib.sha256(b'original encoded recording').hexdigest()
        def upload(data):
            handler = object.__new__(server.Handler)
            handler.path = f'/api/upload?sessionId={self.sid}&chunkIndex=0&sourceSha256={digest}'
            handler.headers = Message(); handler.headers['Content-Type'] = 'audio/wav'
            handler.headers['Content-Length'] = str(len(data)); handler.rfile = BytesIO(data)
            handler._send_json = mock.Mock(); handler.do_POST()
            return handler._send_json.call_args.args
        with mock.patch.object(server, 'enqueue_transcription') as dispatch:
            self.assertEqual(200, upload(blob)[0])
            code, receipt = upload(fixtures.wav_bytes(b'\x02\x00'))
            dispatch.assert_not_called()  # Missing mode defaults to offline.
        self.assertEqual(200, code); self.assertTrue(receipt['duplicate'])
        self.assertEqual(digest, receipt['sourceSha256'])
        self.assertEqual(blob, Path(self.temp.name, self.sid, 'chunk_0000.wav').read_bytes())

    def test_bad_backfill_chunk_does_not_reach_provider_or_stop_other_chunks(self):
        folder = Path(server.init_session(self.sid))
        (folder / 'chunk_0000.webm').write_bytes(b'\x1a\x45\xdf\xa3' + b'\0' * 106)
        (folder / 'chunk_0001.wav').write_bytes(fixtures.wav_bytes())
        with mock.patch.object(server, 'transcribe_with_openai', return_value={'text': 'Synthetic transcript'}) as provider:
            result = server.backfill_missing_transcripts_for_session(self.sid)
        self.assertEqual(1, provider.call_count)
        self.assertEqual(1, result['backfilledChunks'])
        self.assertTrue((folder / 'chunk_0000.webm').exists())
        status = server._read_session_status(self.sid)
        self.assertIn('0', status['transcriptionFailures'])


class CaptureStabilityTests(unittest.TestCase):
    setUp = capture_fixtures.CaptureServerTests.setUp
    def test_late_event_or_heartbeat_cannot_reopen_stopped_session(self):
        server._capture_start(self.sid, {'recorderState': 'recording', 'trackState': 'live'})
        server._capture_begin_finalization(self.sid, 0)
        event = server._capture_materialize_event(self.sid, 'capture_interrupted', {'clientTimestampMs': 1000})
        self.assertFalse(event['sessionOpen'])
        heartbeat = server._capture_heartbeat(self.sid, {'clientState': 'healthy', 'recorderState': 'recording', 'trackState': 'live'})
        self.assertFalse(heartbeat['sessionOpen'])

    def test_old_interruption_event_cannot_undo_recovery(self):
        server._capture_start(self.sid, {'recorderState': 'recording', 'trackState': 'live'})
        server._capture_materialize_event(self.sid, 'capture_recovered', {'clientTimestampMs': 2000})
        event = server._capture_materialize_event(self.sid, 'capture_interrupted', {'clientTimestampMs': 1000})
        self.assertEqual('recovered_with_gap', event['state'])


if __name__ == '__main__':
    unittest.main()

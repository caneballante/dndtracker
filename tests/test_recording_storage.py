from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

import server
from recording_storage import validate_audio


def wav_bytes(sample=b'\x01\x00'):
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(sample * 16000)
    return buffer.getvalue()


class OfflineRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(server, 'UPLOADS_DIR', self.temp.name)
        patch.start()
        self.addCleanup(patch.stop)
        self.sid = '1234567890123'

    def upload(self, blob, index=0, content_type='audio/wav', deferred=True, content_length=None):
        handler = object.__new__(server.Handler)
        handler.path = f'/api/upload?sessionId={self.sid}&chunkIndex={index}&deferTranscription={int(deferred)}'
        handler.headers = Message()
        handler.headers['Content-Type'] = content_type
        handler.headers['Content-Length'] = str(len(blob) if content_length is None else content_length)
        handler.rfile = BytesIO(blob)
        handler._send_json = mock.Mock()
        handler.do_POST()
        return handler._send_json.call_args.args

    def test_offline_upload_saves_complete_audio_without_starting_ai(self):
        blob = wav_bytes()
        with mock.patch.object(server, 'enqueue_transcription') as thread:
            code, response = self.upload(blob)
        self.assertEqual(200, code, response)
        thread.assert_not_called()
        folder = Path(self.temp.name, self.sid)
        self.assertEqual(blob, (folder / response['filename']).read_bytes())
        self.assertEqual('queued', json.loads((folder / 'status.json').read_text())['chunks'][0]['transcriptionStatus'])
        self.assertEqual([], list(folder.glob('*.tmp')))
        self.assertEqual(1, len(server._missing_transcript_audio_chunks(self.sid)))

    def test_repeated_acknowledgment_is_safe_and_does_not_start_transcription_twice(self):
        blob = wav_bytes()
        with mock.patch.object(server, 'enqueue_transcription') as thread:
            first = self.upload(blob, deferred=False)
            second = self.upload(blob, deferred=False)
        self.assertEqual(200, first[0], first)
        self.assertEqual(200, second[0], second)
        self.assertTrue(second[1]['duplicate'])
        self.assertEqual(1, thread.call_count)

    def test_conflicting_retry_never_overwrites_previous_audio(self):
        blob = wav_bytes()
        self.upload(blob)
        code, response = self.upload(wav_bytes(b'\x02\x00'))
        self.assertEqual(409, code, response)
        self.assertEqual(blob, Path(self.temp.name, self.sid, 'chunk_0000.wav').read_bytes())

    def test_empty_webm_header_and_truncated_wav_are_rejected_before_ai(self):
        for blob, content_type in [(b'\x1a\x45\xdf\xa3' + b'\x00' * 106, 'audio/webm'), (wav_bytes()[:-10], 'audio/wav')]:
            with self.subTest(content_type=content_type), mock.patch.object(server, 'enqueue_transcription') as thread:
                code, response = self.upload(blob, content_type=content_type, deferred=False)
                self.assertEqual(400, code, response)
                thread.assert_not_called()
                self.assertEqual([], list(Path(self.temp.name, self.sid).glob('chunk_*')))

    def test_incomplete_transfer_is_not_acknowledged_or_written(self):
        blob = wav_bytes()
        code, response = self.upload(blob, content_length=len(blob) + 10)
        self.assertEqual(503, code, response)
        self.assertIn('Incomplete', response['error'])
        self.assertEqual([], list(Path(self.temp.name, self.sid).glob('chunk_*')))

    def test_wav_zero_frames_are_not_valid_saved_audio(self):
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'')
        with self.assertRaises(ValueError):
            validate_audio(buffer.getvalue(), 'wav')

    def test_disk_error_is_retryable_and_the_original_chunk_can_be_saved_later(self):
        blob = wav_bytes()
        with mock.patch.object(server, 'write_audio_atomic', side_effect=PermissionError('sharing violation')):
            code, response = self.upload(blob)
        self.assertEqual(503, code, response)
        self.assertEqual(200, self.upload(blob)[0])

    def test_retry_completes_status_when_audio_was_saved_before_status_failed(self):
        blob = wav_bytes()
        with mock.patch.object(server, 'update_status_for_chunk', side_effect=PermissionError('sharing violation')):
            code, response = self.upload(blob)
        self.assertEqual(503, code, response)
        self.assertEqual(blob, Path(self.temp.name, self.sid, 'chunk_0000.wav').read_bytes())
        self.assertEqual(200, self.upload(blob)[0])


if __name__ == '__main__':
    unittest.main()

"""Local, validated, repeatable audio writes. This module makes no network requests."""

import hashlib
import io
import os
import threading
import uuid
import wave

_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


def upload_lock(session_dir):
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(os.path.abspath(session_dir), threading.Lock())


def validate_audio(blob, extension):
    extension = '.' + extension.lstrip('.').lower()
    if extension == '.wav':
        try:
            with wave.open(io.BytesIO(blob), 'rb') as audio:
                frames = audio.getnframes()
                frame_bytes = audio.getnchannels() * audio.getsampwidth()
                if frames < 1 or len(audio.readframes(frames)) != frames * frame_bytes:
                    raise ValueError('Audio chunk contains no complete audio.')
        except (wave.Error, EOFError) as error:
            raise ValueError('Audio chunk is not a readable WAV file.') from error
    elif extension == '.webm':
        # The zero-duration recorder loop emits an EBML/track header without a Cluster.
        if len(blob) < 128 or not blob.startswith(b'\x1a\x45\xdf\xa3') or not _webm_has_audio_block(blob):
            raise ValueError('Audio chunk contains no recorded audio. Restart recording with Start.')
    elif extension == '.ogg':
        offset, audio_payload = 0, False
        while offset < len(blob):
            if blob[offset:offset + 4] != b'OggS' or offset + 27 > len(blob):
                raise ValueError('Invalid or incomplete Ogg page.')
            segments = blob[offset + 26]
            start = offset + 27 + segments
            if start > len(blob):
                raise ValueError('Incomplete Ogg page header.')
            end = start + sum(blob[offset + 27:start])
            if end > len(blob):
                raise ValueError('Incomplete Ogg audio payload.')
            payload = blob[start:end]
            if payload and not payload.startswith((b'OpusHead', b'OpusTags', b'\x01vorbis', b'\x03vorbis', b'\x05vorbis')):
                audio_payload = True
            offset = end
        if not audio_payload:
            raise ValueError('Ogg chunk contains no audio payload.')
    elif extension == '.mp4':
        offset, audio_payload = 0, False
        while offset < len(blob):
            if offset + 8 > len(blob):
                raise ValueError('Incomplete MP4 box.')
            size = int.from_bytes(blob[offset:offset + 4], 'big')
            header = 8
            if size == 1:
                if offset + 16 > len(blob):
                    raise ValueError('Incomplete MP4 extended box.')
                size = int.from_bytes(blob[offset + 8:offset + 16], 'big'); header = 16
            elif size == 0:
                size = len(blob) - offset
            if size < header or offset + size > len(blob):
                raise ValueError('Invalid or incomplete MP4 box.')
            if blob[offset + 4:offset + 8] == b'mdat' and size > header:
                audio_payload = True
            offset += size
        if not audio_payload:
            raise ValueError('MP4 chunk contains no media payload.')
    elif not blob:
        raise ValueError('Empty audio chunk.')


def _webm_has_audio_block(blob):
    """Inspect EBML elements, not a byte-pattern match inside metadata.

    Streaming recorders leave Segment/Cluster sizes unknown. A checkpoint prefix
    is usable if it includes a complete encoded block, even with an open container.
    """
    def vint(position, keep_marker=False):
        if position >= len(blob) or not blob[position]:
            raise ValueError('Invalid EBML integer.')
        width = 1
        while width <= 8 and not blob[position] & (1 << (8 - width)):
            width += 1
        if width > 8 or position + width > len(blob):
            raise ValueError('Incomplete EBML integer.')
        value = int.from_bytes(blob[position:position + width], 'big')
        return (value if keep_marker else value & ((1 << (7 * width)) - 1)), width
    def scan(start, end, depth=0):
        if depth > 4:
            return False
        while start < end:
            identifier, width = vint(start, True)
            length, size_width = vint(start + width)
            body = start + width + size_width
            unknown = length == (1 << (7 * size_width)) - 1
            stop = end if unknown else body + length
            if identifier in {0x18538067, 0x1F43B675, 0xA0}:
                if scan(body, min(stop, end), depth + 1):
                    return True
            elif identifier in {0xA3, 0xA1} and stop <= end:
                _, track_width = vint(body)
                if length > track_width + 3:
                    return True
            if stop > end or stop <= start:
                return False
            start = stop
        return False
    try:
        return scan(0, len(blob))
    except ValueError:
        return False


def write_audio_atomic(path, blob):
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    try:
        with open(temporary, 'xb') as output:
            output.write(blob)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def identical_audio(path, blob):
    if not os.path.isfile(path) or os.path.getsize(path) != len(blob):
        return False
    digest = hashlib.sha256()
    with open(path, 'rb') as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b''):
            digest.update(block)
    return digest.digest() == hashlib.sha256(blob).digest()

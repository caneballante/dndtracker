from email.message import Message
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import unittest
from unittest import mock
from urllib.parse import urlparse

import server


ROOT = Path(__file__).resolve().parents[1]


class LocalAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        url = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
        if url and not urlparse(url).netloc and not urlparse(url).scheme:
            self.paths.add(urlparse(url).path)


class StaticUiAssetTests(unittest.TestCase):
    def handler(self, path):
        # Execute the actual GET handler and file response, without opening a socket.
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.directory = str(ROOT)
        handler.headers = Message()
        handler.command = 'GET'
        handler.wfile = BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._send_json = mock.Mock()
        return handler

    def test_every_local_page_asset_is_served_by_real_handler(self):
        assets = LocalAssets()
        assets.feed((ROOT / 'dnd-audio.html').read_text(encoding='utf-8'))
        self.assertTrue({'/table-ready.css', '/table-ready.js'}.issubset(assets.paths))
        for path in sorted(assets.paths):
            with self.subTest(path=path):
                handler = self.handler(path)
                handler.do_GET()
                handler._send_json.assert_not_called()
                handler.send_response.assert_called_once_with(200)
                self.assertEqual((ROOT / path.lstrip('/')).read_bytes(), handler.wfile.getvalue())

    def test_page_assets_need_no_internet(self):
        class ExternalAssets(HTMLParser):
            def handle_starttag(inner, tag, attributes):
                attrs = dict(attributes)
                url = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
                if url:
                    self.assertFalse(urlparse(url).netloc, f'External page dependency: {url}')
        ExternalAssets().feed((ROOT / 'dnd-audio.html').read_text(encoding='utf-8'))

    def test_theme_asset_content_types_and_query_strings(self):
        for path, mime in [('/table-ready.css?v=4.1', 'text/css'), ('/table-ready.js?v=4.1', 'javascript')]:
            with self.subTest(path=path):
                handler = self.handler(path)
                handler.do_GET()
                handler.send_response.assert_called_once_with(200)
                content_type = next(call.args[1] for call in handler.send_header.call_args_list if call.args[0] == 'Content-type')
                self.assertIn(mime, content_type)

    def test_private_files_unknown_assets_and_traversal_remain_blocked(self):
        for path in ('/.env', '/server.py', '/ai_pricing.json', '/campaigns/parmedia-redux/campaign.json',
                     '/uploads/12345678/party.txt', '/unknown.js', '/../table-ready.js',
                     '/assets/table-ready.css', '/assets%2ftable-ready.css', '/table-ready.js/extra'):
            with self.subTest(path=path):
                handler = self.handler(path)
                handler.do_GET()
                handler._send_json.assert_called_once_with(404, {'ok': False, 'error': 'Not found.'})
                self.assertEqual(b'', handler.wfile.getvalue())

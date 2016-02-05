import os

import asyncio

import aioh2
from . import BaseTestCase, async_test


class TestHelper(BaseTestCase):
    @asyncio.coroutine
    def _cb(self, proto):
        stream_id, headers = yield from proto.recv_request()
        resp = yield from proto.read_stream(stream_id, -1)
        yield from proto.send_headers(stream_id, {':status': '200'})
        yield from proto.send_data(stream_id, b'hello, ')
        yield from proto.send_data(stream_id, resp, end_stream=True)

    @async_test
    def test_tcp(self):
        # Start server
        server = yield from aioh2.start_server(
            lambda p: asyncio.async(self._cb(p)), port=0)
        port = server.sockets[0].getsockname()[1]

        # Send request
        client = yield from aioh2.open_connection('0.0.0.0', port)
        stream_id = yield from client.start_request(
            {':method': 'GET', ':path': '/index.html'})
        name = os.getlogin().encode()
        yield from client.send_data(stream_id, name, end_stream=True)

        # Receive response
        headers = yield from client.recv_response(stream_id)
        self.assertEqual(dict(headers)[':status'], '200')
        resp = yield from client.read_stream(stream_id, -1)
        self.assertEqual(resp, b'hello, ' + name)

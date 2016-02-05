import aioh2
from . import BaseTestCase, async_test


class TestHelper(BaseTestCase):
    @async_test
    def test_tcp(self):
        s = [None]

        def cb(proto):
            s[0] = proto

        server = yield from aioh2.start_server(cb, port=0)
        port = server.sockets[0].getsockname()[1]
        client = yield from aioh2.open_connection('0.0.0.0', port)
        stream_id = yield from client.start_request(
            {':method': 'GET', ':path': '/index.html'})
        yield from client.send_data(stream_id, b'hello world', end_stream=True)

        stream_id, headers = yield from s[0].recv_request()
        resp = yield from s[0].read_stream(stream_id, -1)
        self.assertEqual(resp, b'hello world')

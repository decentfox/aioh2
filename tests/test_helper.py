import uuid

import asyncio

import aioh2
from . import BaseTestCase, async_test


class TestHelper(BaseTestCase):
    @asyncio.coroutine
    def _cb(self, proto):
        while True:
            stream_id, headers = yield from proto.recv_request()
            # print('  < REQ {}'.format(stream_id))
            yield from proto.start_response(stream_id, [(':status', '200')])
            yield from proto.send_data(stream_id, b'hello, ')
            resp = yield from proto.read_stream(stream_id, -1)
            yield from proto.send_data(stream_id, resp)
            # yield from asyncio.sleep(0.1)
            # print('  > REP {}'.format(stream_id))
            yield from proto.send_trailers(stream_id, [('len', str(len(resp)))])

    @async_test
    def test_tcp(self):
        # Start server
        # FIXME concurrency=3 should work and block the consumer. Figure out why
        # it does not work.
        server = yield from aioh2.start_server(self._cb, host='127.0.0.1',
                                               port=0, concurrency=8)
        port = server.sockets[0].getsockname()[1]

        client = yield from aioh2.open_connection('127.0.0.1', port)
        client.functional_timeout = 0.1
        yield from asyncio.sleep(0.2)

        @asyncio.coroutine
        def _test():
            rtt = yield from client.wait_functional()
            self.assertIsNotNone(rtt)
            self.assertNotAlmostEqual(rtt, self.loop.time(), places=1)
            # Send request
            stream_id = yield from client.start_request([
                (':scheme', 'h2c'),
                (':authority', 'example.com'),
                (':method', 'GET'),
                (':path', '/index.html'),
            ])
            # print('> REQ {}'.format(stream_id))
            name = uuid.uuid4().bytes
            yield from client.send_data(stream_id, name, end_stream=True)

            # Receive response
            headers = yield from client.recv_response(stream_id)
            self.assertEqual(dict(headers)[':status'], '200')
            resp = yield from client.read_stream(stream_id, -1)
            # print('< REP {}'.format(stream_id))
            self.assertEqual(resp, b'hello, ' + name)
            trailers = yield from client.recv_trailers(stream_id)
            self.assertEqual(dict(trailers)['len'], str(len(name)))

        fs, _ = yield from asyncio.wait([_test() for _ in range(8)])
        for f in fs:
            f.result()
        client.close_connection()
        yield from asyncio.sleep(0.1)

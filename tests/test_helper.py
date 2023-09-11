import uuid

import asyncio

import aioh2
from . import BaseTestCase, async_test


class TestHelper(BaseTestCase):

    async def _cb(self, proto):
        while True:
            stream_id, headers = await proto.recv_request()
            # print('  < REQ {}'.format(stream_id))
            await proto.start_response(stream_id, [(':status', '200')])
            await proto.send_data(stream_id, b'hello, ')
            resp = await proto.read_stream(stream_id, -1)
            await proto.send_data(stream_id, resp)
            # await asyncio.sleep(0.1)
            # print('  > REP {}'.format(stream_id))
            await proto.send_trailers(stream_id, [('len', str(len(resp)))])

    @async_test
    async def test_tcp(self):
        # Start server
        # FIXME concurrency=3 should work and block the consumer. Figure out why
        # it does not work.
        server = await aioh2.start_server(self._cb, host='127.0.0.1',
                                               port=0, concurrency=8)
        port = server.sockets[0].getsockname()[1]

        client = await aioh2.open_connection('127.0.0.1', port)
        client.functional_timeout = 0.1
        await asyncio.sleep(0.2)

        async def _test():
            rtt = await client.wait_functional()
            self.assertIsNotNone(rtt)
            self.assertNotAlmostEqual(rtt, self.loop.time(), places=1)
            # Send request
            stream_id = await client.start_request([
                (':scheme', 'h2c'),
                (':authority', 'example.com'),
                (':method', 'GET'),
                (':path', '/index.html'),
            ])
            # print('> REQ {}'.format(stream_id))
            name = uuid.uuid4().bytes
            await client.send_data(stream_id, name, end_stream=True)

            # Receive response
            headers = await client.recv_response(stream_id)
            self.assertEqual(dict(headers)[':status'], '200')
            resp = await client.read_stream(stream_id, -1)
            # print('< REP {}'.format(stream_id))
            self.assertEqual(resp, b'hello, ' + name)
            trailers = await client.recv_trailers(stream_id)
            self.assertEqual(dict(trailers)['len'], str(len(name)))

        fs, _ = await asyncio.wait([asyncio.create_task(_test()) for _ in range(8)])
        for f in fs:
            f.result()
        client.close_connection()
        await asyncio.sleep(0.1)

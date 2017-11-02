import functools
import os
import unittest
import uuid

import asyncio
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import RemoteSettingsChanged, RequestReceived, WindowUpdated
from h2.events import SettingsAcknowledged

from aioh2 import H2Protocol


def async_test(timeout=1):
    func = None
    if callable(timeout):
        func = timeout
        timeout = 1

    def _decorator(f):
        @functools.wraps(f)
        def _wrapper(self, *args, **kwargs):
            task = self.loop.create_task(
                asyncio.coroutine(f)(self, *args, **kwargs))

            def _cancel():
                task.print_stack()
                task.cancel()

            time_handle = self.loop.call_later(timeout, _cancel)
            try:
                return self.loop.run_until_complete(task)
            except asyncio.CancelledError:
                events = []
                while True:
                    try:
                        events.append(self.server.events.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                self.fail('server events: {}'.format(events))
            finally:
                time_handle.cancel()

        return _wrapper

    if func is not None:
        return _decorator(func)

    return _decorator


class Server(H2Protocol):
    def __init__(self, test, client_side, *, loop=None):
        super().__init__(client_side, loop=loop)
        test.server = self
        self.events = asyncio.Queue()

    def _event_received(self, event):
        self.events.put_nowait(event)
        super()._event_received(event)


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.path = os.path.join('/tmp', uuid.uuid4().hex)
        self.server = None
        self._server = self.loop.run_until_complete(
            self.loop.create_unix_server(
                lambda: Server(self, False, loop=self.loop), self.path))
        self.loop.run_until_complete(self._setUp())

    def tearDown(self):
        self._server.close()
        os.remove(self.path)
        self.w.close()

    @asyncio.coroutine
    def _setUp(self):
        self.r, self.w = yield from asyncio.open_unix_connection(self.path)
        config = H2Configuration(header_encoding='utf-8')
        self.conn = H2Connection(config=config)
        self.conn.initiate_connection()
        self.w.write(self.conn.data_to_send())
        events = yield from self._expect_events(3)
        self.assertIsInstance(events[0], RemoteSettingsChanged)
        self.assertIsInstance(events[1], RemoteSettingsChanged)
        self.assertIsInstance(events[2], SettingsAcknowledged)

        self.assertIsInstance((yield from self.server.events.get()),
                              RemoteSettingsChanged)
        self.assertIsInstance((yield from self.server.events.get()),
                              SettingsAcknowledged)
        self.assertIsInstance((yield from self.server.events.get()),
                              SettingsAcknowledged)

    @asyncio.coroutine
    def _expect_events(self, n=1):
        events = []
        self.w.write(self.conn.data_to_send())
        while len(events) < n:
            events += self.conn.receive_data((yield from self.r.read(1024)))
            self.w.write(self.conn.data_to_send())
        self.assertEqual(len(events), n)
        return events

    @asyncio.coroutine
    def _send_headers(self, end_stream=False):
        headers = [
            (':method', 'GET'),
            (':authority', 'example.com'),
            (':scheme', 'h2c'),
            (':path', '/index.html'),
        ]
        stream_id = self.conn.get_next_available_stream_id()
        self.conn.send_headers(stream_id, headers, end_stream=end_stream)
        yield from self._expect_events(0)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, RequestReceived)
        self.assertEqual(event.stream_id, stream_id)
        self.assertEqual(event.headers, headers)
        return stream_id

    @asyncio.coroutine
    def _expect_connection_flow_control_disabled(self):
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], WindowUpdated)
        self.assertEqual(events[0].stream_id, 0)
        self.assertGreater(events[0].delta, 1073741822)

    @asyncio.coroutine
    def _assert_received(self, stream_id, coro, expected):
        data = yield from coro
        self.assertEqual(data, expected)

        ack = 0
        while ack < len(data):
            events = yield from self._expect_events()
            self.assertIsInstance(events[0], WindowUpdated)
            self.assertEqual(events[0].stream_id, stream_id)
            ack += events[0].delta
        self.assertEqual(ack, len(data))

#!/usr/bin/env python

"""
test_aioh2
----------------------------------

Tests for `aioh2` module.
"""
import random
import unittest
import uuid

import asyncio
from h2.events import DataReceived
from h2.events import PingAcknowledged
from h2.events import RemoteSettingsChanged
from h2.events import ResponseReceived
from h2.events import SettingsAcknowledged
from h2.exceptions import FlowControlError
from h2.settings import SettingCodes

from aioh2 import SendException
from aioh2.helper import async_task
from . import async_test, BaseTestCase


class TestServer(BaseTestCase):
    def test_connect(self):
        pass

    @async_test
    def test_ping(self):
        opaque_data = uuid.uuid4().bytes[:8]
        self.conn.ping(opaque_data)
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], PingAcknowledged)
        self.assertEqual(events[0].ping_data, opaque_data)

    @async_test
    def test_request_headers(self):
        yield from self._send_headers()

    @asyncio.coroutine
    def _test_read_frame(self, *, more, end_stream):
        stream_id = yield from self._send_headers()

        data = b'x' * random.randint(128, 512)
        self.conn.send_data(stream_id, data,
                            end_stream=not more and end_stream)
        extra = b''
        if more:
            extra = b'y' * random.randint(128, 512)
            self.conn.send_data(stream_id, extra, end_stream=end_stream)

        yield from self._expect_connection_flow_control_disabled()

        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id), data)

        if more:
            yield from self._assert_received(
                stream_id, self.server.read_stream(stream_id), extra)

        if end_stream:
            frame = yield from self.server.read_stream(stream_id)
            self.assertEqual(frame, b'')
        else:
            try:
                yield from asyncio.wait_for(
                    self.server.read_stream(stream_id), 0.1)
            except asyncio.TimeoutError:
                pass
            else:
                self.assertRaises(asyncio.TimeoutError, lambda: None)

    @async_test
    def test_read_frame(self):
        yield from self._test_read_frame(more=True, end_stream=False)

    @async_test
    def test_read_frame_close(self):
        yield from self._test_read_frame(more=True, end_stream=True)

    @async_test
    def test_read_only_frame(self):
        yield from self._test_read_frame(more=False, end_stream=False)

    @async_test
    def test_read_only_frame_close(self):
        yield from self._test_read_frame(more=False, end_stream=True)

    @asyncio.coroutine
    def _test_read_all(self, *, more, end_stream):
        stream_id = yield from self._send_headers()

        data = b'x' * random.randint(128, 512)
        self.conn.send_data(stream_id, data,
                            end_stream=not more and end_stream)
        if more:
            extra = b'y' * random.randint(128, 512)
            self.conn.send_data(stream_id, extra, end_stream=end_stream)
            data += extra

        yield from self._expect_connection_flow_control_disabled()

        if end_stream:
            yield from self._assert_received(
                stream_id, self.server.read_stream(stream_id, -1), data)

            frame = yield from self.server.read_stream(stream_id, -1)
            self.assertEqual(frame, b'')
        else:
            try:
                yield from asyncio.wait_for(
                    self.server.read_stream(stream_id, -1), 0.1)
            except asyncio.TimeoutError:
                pass
            else:
                self.assertRaises(asyncio.TimeoutError, lambda: None)

    @async_test
    def test_read_all(self):
        yield from self._test_read_all(more=True, end_stream=False)

    @async_test
    def test_read_all_close(self):
        yield from self._test_read_all(more=True, end_stream=True)

    @async_test
    def test_read_all_only_frame(self):
        yield from self._test_read_all(more=False, end_stream=False)

    @async_test
    def test_read_all_only_frame_close(self):
        yield from self._test_read_all(more=False, end_stream=True)

    @asyncio.coroutine
    def _test_read_exactly(self, *, empty, explicit_close):
        stream_id = yield from self._send_headers()

        self.conn.send_data(stream_id, b'333')
        self.conn.send_data(stream_id, b'55555')
        if empty:
            self.conn.send_data(stream_id, b'')

        yield from self._expect_connection_flow_control_disabled()

        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 2), b'33')

        self.conn.send_data(stream_id, b'88888888',
                            end_stream=not explicit_close)
        if explicit_close:
            self.conn.end_stream(stream_id)
        yield from self._expect_events(0)

        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 8), b'35555588')
        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 2), b'88')
        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 8), b'8888')

    @async_test
    def test_read_exactly(self):
        yield from self._test_read_exactly(empty=False, explicit_close=False)

    @async_test
    def test_read_exactly_empty_frame(self):
        yield from self._test_read_exactly(empty=True, explicit_close=False)

    @async_test
    def test_read_exactly_explicit_close(self):
        yield from self._test_read_exactly(empty=True, explicit_close=True)

    @async_test
    def test_read_exactly_empty_frame_explicit_close(self):
        yield from self._test_read_exactly(empty=True, explicit_close=True)

    @async_test
    def test_flow_control_settings(self):
        self.server.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 3})
        event = yield from self._expect_events()
        self.assertIsInstance(event[0], RemoteSettingsChanged)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, SettingsAcknowledged)

        stream_id = yield from self._send_headers()
        self.conn.send_data(stream_id, b'xx')

        yield from self._expect_connection_flow_control_disabled()

        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 2), b'xx')

        self.conn.send_data(stream_id, b'xxx')
        yield from self._expect_events(0)
        yield from self._assert_received(
            stream_id, self.server.read_stream(stream_id, 3), b'xxx')

        self.assertRaises(FlowControlError,
                          self.conn.send_data, stream_id, b'xxxx')

    @async_test
    def test_flow_control(self):
        self.conn.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 3})
        event = yield from self._expect_events()
        self.assertIsInstance(event[0], SettingsAcknowledged)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, RemoteSettingsChanged)

        stream_id = yield from self._send_headers(end_stream=True)
        yield from self.server.start_response(stream_id, [(':status', '200')])
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], ResponseReceived)

        yield from self.server.send_data(stream_id, b'12')
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], DataReceived)
        self.assertEqual(events[0].data, b'12')

        try:
            yield from asyncio.wait_for(
                self.server.send_data(stream_id, b'34'), 0.1)
        except asyncio.TimeoutError:
            events = yield from self._expect_events()
            self.assertIsInstance(events[0], DataReceived)
            self.assertEqual(events[0].data, b'3')
        else:
            self.assertRaises(asyncio.TimeoutError, lambda: None)

        self.conn.increment_flow_control_window(3, stream_id=stream_id)
        yield from self._expect_events(0)

        try:
            yield from asyncio.wait_for(
                self.server.send_data(stream_id, b'5678'), 0.1)
        except asyncio.TimeoutError:
            events = yield from self._expect_events()
            self.assertIsInstance(events[0], DataReceived)
            self.assertEqual(events[0].data, b'567')
        else:
            self.assertRaises(asyncio.TimeoutError, lambda: None)

    @async_test
    def test_broken_send(self):
        self.conn.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 3})
        event = yield from self._expect_events()
        self.assertIsInstance(event[0], SettingsAcknowledged)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, RemoteSettingsChanged)

        stream_id = yield from self._send_headers(end_stream=True)
        yield from self.server.start_response(stream_id, [(':status', '200')])
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], ResponseReceived)

        yield from self.server.send_data(stream_id, b'12')
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], DataReceived)
        self.assertEqual(events[0].data, b'12')

        f = async_task(self.server.send_data(stream_id, b'345678'))
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], DataReceived)
        self.assertEqual(events[0].data, b'3')

        self.conn.reset_stream(stream_id)
        yield from self._expect_events(0)

        try:
            yield from f
        except SendException as e:
            self.assertEqual(e.data, b'45678')
        else:
            self.assertRaises(SendException, lambda: None)

    @async_test(timeout=8)
    def test_priority(self):
        self.conn.update_settings({
            SettingCodes.MAX_FRAME_SIZE: 16384,
            SettingCodes.INITIAL_WINDOW_SIZE: 16384 * 1024 * 32,
        })
        event = yield from self._expect_events()
        self.assertIsInstance(event[0], SettingsAcknowledged)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, RemoteSettingsChanged)

        stream_1 = yield from self._send_headers()
        yield from self.server.start_response(stream_1, [(':status', '200')])
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], ResponseReceived)

        stream_2 = yield from self._send_headers()
        yield from self.server.start_response(stream_2, [(':status', '200')])
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], ResponseReceived)

        p1 = 32
        p2 = 20

        self.server.reprioritize(stream_1, weight=p1)
        self.server.reprioritize(stream_2, weight=p2)
        self.server.pause_writing()

        running = [True]

        @asyncio.coroutine
        def _write(stream_id):
            count = 0
            while running[0]:
                yield from self.server.send_data(stream_id, b'x')
                count += 1
            yield from self.server.end_stream(stream_id)
            return count

        task_1 = async_task(_write(stream_1))
        task_2 = async_task(_write(stream_2))

        for i in range(1000):
            self.server.resume_writing()
            yield from asyncio.sleep(0.004)
            self.server.pause_writing()
            yield from asyncio.sleep(0.001)

        running[0] = False
        self.server.resume_writing()

        count_1 = yield from task_1
        count_2 = yield from task_2

        self.assertAlmostEqual(count_1 / count_2, p1 / p2, 1)


if __name__ == '__main__':
    import sys

    sys.exit(unittest.main())

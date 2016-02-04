#!/usr/bin/env python

"""
test_aioh2
----------------------------------

Tests for `aioh2` module.
"""
import unittest
import uuid

from h2.events import PingAcknowledged, RequestReceived

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
        headers = [(':method', 'GET'), (':path', '/index.html')]
        stream_id = self.conn.get_next_available_stream_id()
        self.conn.send_headers(stream_id, headers)
        yield from self._expect_events(0)
        event = yield from self.server.events.get()
        self.assertIsInstance(event, RequestReceived)
        self.assertEqual(event.stream_id, stream_id)
        self.assertEqual(event.headers, headers)


if __name__ == '__main__':
    import sys

    sys.exit(unittest.main())

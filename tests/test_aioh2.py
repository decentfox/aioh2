#!/usr/bin/env python

"""
test_aioh2
----------------------------------

Tests for `aioh2` module.
"""
import unittest
import uuid

from h2.events import PingAcknowledged

from . import async_test, BaseTestCase


class TestServer(BaseTestCase):
    @async_test
    def test_connect(self):
        pass

    @async_test
    def test_ping(self):
        opaque_data = uuid.uuid4().bytes[:8]
        self.conn.ping(opaque_data)
        events = yield from self._expect_events()
        self.assertIsInstance(events[0], PingAcknowledged)
        self.assertEqual(events[0].ping_data, opaque_data)


if __name__ == '__main__':
    import sys

    sys.exit(unittest.main())

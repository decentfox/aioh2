#!/usr/bin/env python

"""
test_aioh2
----------------------------------

Tests for `aioh2` module.
"""
import os
import unittest
import uuid

import asyncio
from h2.connection import H2Connection
from h2.events import RemoteSettingsChanged, SettingsAcknowledged

from aioh2 import H2Protocol
from . import async_test


class Server(H2Protocol):
    pass


class TestServer(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.path = os.path.join('/tmp', uuid.uuid4().hex)
        self.server = self.loop.run_until_complete(
            self.loop.create_unix_server(
                lambda: Server(False, loop=self.loop), self.path))

    def tearDown(self):
        self.server.close()
        os.remove(self.path)

    @asyncio.coroutine
    def _connect(self):
        r, w = yield from asyncio.open_unix_connection(self.path)
        conn = H2Connection()
        conn.initiate_connection()
        w.write(conn.data_to_send())

        events = []
        while len(events) < 2:
            events += conn.receive_data((yield from r.read(1024)))
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], RemoteSettingsChanged)
        self.assertIsInstance(events[1], SettingsAcknowledged)

    @async_test()
    def test_connect(self):
        yield from self._connect()


if __name__ == '__main__':
    import sys

    sys.exit(unittest.main())

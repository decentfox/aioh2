import asyncio
from collections import defaultdict
from functools import partial
from logging import getLogger

from h2 import events
from h2 import settings
from h2.connection import H2Connection
from h2.exceptions import StreamClosedError

logger = getLogger(__package__)


class H2Protocol(asyncio.Protocol):
    def __init__(self, client_side, *, loop=None):
        if loop is None:
            loop = asyncio.get_event_loop()
        self._conn = H2Connection(client_side=client_side)
        self._transport = None
        self._resumed = asyncio.Event(loop=loop)
        self._window_open = asyncio.Event(loop=loop)
        self._stream_windows = defaultdict(partial(asyncio.Event, loop=loop))
        self._event_handlers = {
            events.RequestReceived: self._request_received,
            events.ResponseReceived: self._response_received,
            events.TrailersReceived: self._trailers_received,
            events.DataReceived: self._data_received,
            events.WindowUpdated: self._window_updated,
            events.RemoteSettingsChanged: self._remote_settings_changed,
            events.PingAcknowledged: self._ping_acknowledged,
            events.StreamEnded: self._stream_ended,
            events.StreamReset: self._stream_reset,
            events.PushedStreamReceived: self._pushed_stream_received,
            events.SettingsAcknowledged: self._settings_acknowledged,
            events.PriorityUpdated: self._priority_updated,
            events.ConnectionTerminated: self._connection_terminated,
        }

    # asyncio protocol

    def connection_made(self, transport):
        self._transport = transport
        self._conn.initiate_connection()
        self._flush()
        self._sync_window_open()
        self.resume_writing()

    def connection_lost(self, exc):
        self._conn = None
        self._transport = None
        self.pause_writing()

    def pause_writing(self):
        self._resumed.clear()

    def resume_writing(self):
        self._resumed.set()

    def data_received(self, data):
        events_ = self._conn.receive_data(data)
        self._flush()
        for event in events_:
            self._event_handlers[type(event)](event)

    def eof_received(self):
        self._conn.close_connection()
        self._flush()

    # hyper-h2 event handlers

    def _request_received(self, event: events.RequestReceived):
        self._sync_window_open(event.stream_id)
        self.request_received(event.stream_id, event.headers)

    def _response_received(self, event: events.ResponseReceived):
        self.response_received(event.stream_id, event.headers)

    def _trailers_received(self, event: events.TrailersReceived):
        self.trailers_received(event.stream_id, event.headers)

    def _data_received(self, event: events.DataReceived):
        pass

    def _window_updated(self, event: events.WindowUpdated):
        self._sync_window_open(event.stream_id or None)

    def _remote_settings_changed(self, event: events.RemoteSettingsChanged):
        if settings.INITIAL_WINDOW_SIZE in event.changed_settings:
            self._sync_window_open()

    def _ping_acknowledged(self, event: events.PingAcknowledged):
        pass

    def _stream_ended(self, event: events.StreamEnded):
        self._sync_window_open(event.stream_id)

    def _stream_reset(self, event: events.StreamReset):
        self._sync_window_open(event.stream_id)

    def _pushed_stream_received(self, event: events.PushedStreamReceived):
        pass

    def _settings_acknowledged(self, event: events.SettingsAcknowledged):
        pass

    def _priority_updated(self, event: events.PriorityUpdated):
        pass

    def _connection_terminated(self, event: events.ConnectionTerminated):
        logger.warning('Remote peer sent GOAWAY [ERR: %s], disconnect now.',
                       event.error_code)
        self._transport.close()

    def _flush(self):
        self._transport.write(self._conn.data_to_send())

    def _sync_window_open(self, stream_id=None):
        if self._conn.outbound_flow_control_window > 0:
            self._window_open.set()
        else:
            self._window_open.clear()
        if stream_id is not None:
            try:
                window = self._conn.local_flow_control_window(stream_id)
            except StreamClosedError:
                self._stream_windows.pop(stream_id)
            else:
                if window > 0:
                    self._stream_windows[stream_id].set()
                else:
                    self._stream_windows[stream_id].clear()

    @asyncio.coroutine
    def start_request(self, headers, end_stream=False):
        yield from self._resumed.wait()
        stream_id = self._conn.get_next_available_stream_id()
        self._conn.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()
        self._sync_window_open(stream_id)
        return stream_id

    @asyncio.coroutine
    def send_data(self, stream_id, data, end_stream=False):
        while True:
            yield from self._resumed.wait()
            data_size = len(data)
            size = min(data_size,
                       self._conn.local_flow_control_window(stream_id),
                       self._conn.max_outbound_frame_size)
            if data_size == 0 or size == data_size:
                self._conn.send_data(stream_id, data, end_stream=end_stream)
                self._flush()
                self._sync_window_open(stream_id)
                return
            elif size > 0:
                self._conn.send_data(stream_id, data[:size])
                data = data[size:]
                self._flush()
                self._sync_window_open(stream_id)
            yield from self._window_open.wait()
            yield from self._stream_windows[stream_id].wait()

    @asyncio.coroutine
    def send_headers(self, stream_id, headers, end_stream=False):
        yield from self._resumed.wait()
        self._conn.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()

    @asyncio.coroutine
    def end_stream(self, stream_id):
        yield from self._resumed.wait()
        self._conn.end_stream(stream_id)
        self._flush()
        self._sync_window_open(stream_id)

    def request_received(self, stream_id, headers):
        pass

    def response_received(self, stream_id, headers):
        pass

    def trailers_received(self, stream_id, headers):
        pass

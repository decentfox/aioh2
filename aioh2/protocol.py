import struct
from collections import deque
from logging import getLogger

import asyncio
import priority
from h2 import events
from h2 import settings
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.exceptions import NoSuchStreamError, StreamClosedError, ProtocolError

from . import exceptions

__all__ = ['H2Protocol']
logger = getLogger(__package__)


@asyncio.coroutine
def _wait_for_events(*events_):
    while not all([event.is_set() for event in events_]):
        yield from asyncio.wait([event.wait() for event in events_])


class _StreamEndedException(Exception):
    def __init__(self, bufs=None):
        if bufs is None:
            bufs = []
        self.bufs = bufs


class CallableEvent(asyncio.Event):
    def __init__(self, func, *, loop=None):
        super().__init__(loop=loop)
        self._func = func

    @asyncio.coroutine
    def wait(self):
        while not self._func():
            self.clear()
            yield from super().wait()

    def sync(self):
        if self._func():
            self.set()
        else:
            self.clear()

    def is_set(self):
        self.sync()
        return super().is_set()


class H2Stream:
    def __init__(self, stream_id, window_getter, loop=None):
        if loop is None:
            loop = asyncio.get_event_loop()
        self._stream_id = stream_id
        self._window_getter = window_getter

        self._wlock = asyncio.Lock(loop=loop)
        self._window_open = CallableEvent(self._is_window_open, loop=loop)

        self._rlock = asyncio.Lock(loop=loop)
        self._buffers = deque()
        self._buffer_size = 0
        self._buffer_ready = asyncio.Event(loop=loop)
        self._response = asyncio.Future(loop=loop)
        self._trailers = asyncio.Future(loop=loop)
        self._eof_received = False
        self._closed = False

    @property
    def id(self):
        return self._stream_id

    @property
    def window_open(self):
        return self._window_open

    @property
    def rlock(self):
        return self._rlock

    @property
    def wlock(self):
        return self._wlock

    @property
    def buffer_size(self):
        return self._buffer_size

    @property
    def response(self):
        return self._response

    @property
    def trailers(self):
        return self._trailers

    def _is_window_open(self):
        try:
            window = self._window_getter(self._stream_id)
        except NoSuchStreamError:
            self._closed = True
            return True
        else:
            return window > 0

    def feed_data(self, data):
        if data:
            self._buffers.append(data)
            self._buffer_size += len(data)
            self._buffer_ready.set()

    def feed_eof(self):
        self._eof_received = True
        self._buffer_ready.set()
        self.feed_trailers({})

    def feed_response(self, headers):
        self._response.set_result(headers)

    def feed_trailers(self, headers):
        if not self._trailers.done():
            self._trailers.set_result(headers)

    @asyncio.coroutine
    def read_frame(self):
        yield from self._buffer_ready.wait()
        rv = b''
        if self._buffers:
            rv = self._buffers.popleft()
            self._buffer_size -= len(rv)
        if not self._buffers:
            if self._eof_received:
                raise _StreamEndedException([rv])
            else:
                self._buffer_ready.clear()
        return rv

    @asyncio.coroutine
    def read_all(self):
        yield from self._buffer_ready.wait()
        rv = []
        rv.extend(self._buffers)
        self._buffers.clear()
        self._buffer_size = 0
        if self._eof_received:
            raise _StreamEndedException(rv)
        else:
            self._buffer_ready.clear()
            return rv

    @asyncio.coroutine
    def read(self, n):
        yield from self._buffer_ready.wait()
        rv = []
        count = 0
        while n > count and self._buffers:
            buf = self._buffers.popleft()
            count += len(buf)
            if n < count:
                rv.append(buf[:n - count])
                self._buffers.appendleft(buf[n - count:])
                count = n
            else:
                rv.append(buf)
        self._buffer_size -= count
        if not self._buffers:
            if self._eof_received:
                raise _StreamEndedException(rv)
            else:
                self._buffer_ready.clear()
        return rv, count


class H2Protocol(asyncio.Protocol):
    def __init__(self, client_side: bool, *, loop=None,
                 concurrency=8, functional_timeout=2):
        if loop is None:
            loop = asyncio.get_event_loop()
        self._loop = loop
        config = H2Configuration(client_side=client_side,
                                 header_encoding='utf-8')
        self._conn = H2Connection(config=config)
        self._transport = None
        self._streams = {}
        self._inbound_requests = asyncio.Queue(concurrency, loop=loop)
        self._priority = priority.PriorityTree()
        self._priority_events = {}
        self._handler = None

        # Locks

        self._is_resumed = False
        self._resumed = CallableEvent(lambda: self._is_resumed, loop=loop)
        self._stream_creatable = CallableEvent(self._is_stream_creatable,
                                               loop=loop)
        self._last_active = 0
        self._ping_index = -1
        self._ping_time = 0
        self._rtt = None
        self._functional_timeout = functional_timeout
        self._functional = CallableEvent(self._is_functional, loop=loop)

        # Dispatch table

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
        self._conn.update_settings({
            settings.SettingCodes.MAX_CONCURRENT_STREAMS: self._inbound_requests.maxsize})
        self._flush()
        self._stream_creatable.sync()
        self.resume_writing()
        self._last_active = self._loop.time()
        self._functional.sync()

    def connection_lost(self, exc):
        self._conn = None
        self._transport = None
        self.pause_writing()
        if self._handler:
            self._handler.cancel()

    def pause_writing(self):
        self._is_resumed = False
        self._resumed.sync()

    def resume_writing(self):
        self._is_resumed = True
        self._resumed.sync()

    def data_received(self, data):
        self._last_active = self._loop.time()
        self._functional.sync()
        events_ = self._conn.receive_data(data)
        self._flush()
        for event in events_:
            self._event_received(event)

    def eof_received(self):
        self._conn.close_connection()
        self._flush()

    # hyper-h2 event handlers

    def _event_received(self, event):
        self._event_handlers[type(event)](event)

    def _request_received(self, event: events.RequestReceived):
        self._inbound_requests.put_nowait((0, event.stream_id, event.headers))
        self._priority.insert_stream(event.stream_id)
        self._priority.block(event.stream_id)

    def _response_received(self, event: events.ResponseReceived):
        self._get_stream(event.stream_id).feed_response(event.headers)

    def _trailers_received(self, event: events.TrailersReceived):
        self._get_stream(event.stream_id).feed_trailers(event.headers)

    def _data_received(self, event: events.DataReceived):
        self._get_stream(event.stream_id).feed_data(event.data)
        if self._conn.inbound_flow_control_window < 1073741823:
            self._conn.increment_flow_control_window(
                2 ** 31 - 1 - self._conn.inbound_flow_control_window)
            self._flush()

    def _window_updated(self, event: events.WindowUpdated):
        if event.stream_id:
            self._get_stream(event.stream_id).window_open.sync()
        else:
            for stream in list(self._streams.values()):
                stream.window_open.sync()

    def _remote_settings_changed(self, event: events.RemoteSettingsChanged):
        if settings.SettingCodes.INITIAL_WINDOW_SIZE in event.changed_settings:
            for stream in list(self._streams.values()):
                stream.window_open.sync()
        if settings.SettingCodes.MAX_CONCURRENT_STREAMS in event.changed_settings:
            self._stream_creatable.sync()

    def _ping_acknowledged(self, event: events.PingAcknowledged):
        if struct.unpack('Q', event.ping_data) == (self._ping_index,):
            self._rtt = self._loop.time() - self._ping_time

    def _stream_ended(self, event: events.StreamEnded):
        self._get_stream(event.stream_id).feed_eof()
        self._stream_creatable.sync()

    def _stream_reset(self, event: events.StreamReset):
        self._get_stream(event.stream_id).window_open.set()
        self._stream_creatable.sync()

    def _pushed_stream_received(self, event: events.PushedStreamReceived):
        pass

    def _settings_acknowledged(self, event: events.SettingsAcknowledged):
        pass

    def _priority_updated(self, event: events.PriorityUpdated):
        self._priority.reprioritize(
            event.stream_id, event.depends_on, event.weight, event.exclusive)

    def _connection_terminated(self, event: events.ConnectionTerminated):
        logger.warning('Remote peer sent GOAWAY [ERR: %s], disconnect now.',
                       event.error_code)
        self._transport.close()

    # Internals

    def _get_stream(self, stream_id):
        stream = self._streams.get(stream_id)
        if stream is None:
            stream = self._streams[stream_id] = H2Stream(
                stream_id, self._conn.local_flow_control_window,
                loop=self._loop)
        return stream

    def _flush(self):
        self._transport.write(self._conn.data_to_send())

    def _is_stream_creatable(self):
        return (self._conn.open_outbound_streams <
                self._conn.remote_settings.max_concurrent_streams)

    def _flow_control(self, stream_id):
        delta = (self._conn.local_settings.initial_window_size -
                 self._get_stream(stream_id).buffer_size -
                 self._conn.remote_flow_control_window(stream_id))
        if delta > 0:
            self._conn.increment_flow_control_window(delta, stream_id)
            self._flush()

    def _is_functional(self):
        return self._last_active + self._functional_timeout > self._loop.time()

    def _priority_step(self):
        # noinspection PyBroadException
        try:
            for stream_id in self._priority:
                fut = self._priority_events.pop(stream_id, None)
                if fut is not None:
                    fut.set_result(None)
                    break
        except Exception:
            if self._priority_events:
                self._priority_events.popitem()[1].set_result(None)

    # APIs

    def set_handler(self, handler):
        """
        Connect with a coroutine, which is scheduled when connection is made.

        This function will create a task, and when connection is closed,
        the task will be canceled.
        :param handler:
        :return: None
        """
        if self._handler:
            raise Exception('Handler was already set')
        if handler:
            self._handler = asyncio.async(handler, loop=self._loop)

    def close_connection(self):
        self._transport.close()

    @asyncio.coroutine
    def start_request(self, headers, *, end_stream=False):
        """
        Start a request by sending given headers on a new stream, and return
        the ID of the new stream.

        This may block until the underlying transport becomes writable, and
        the number of concurrent outbound requests (open outbound streams) is
        less than the value of peer config MAX_CONCURRENT_STREAMS.

        The completion of the call to this method does not mean the request is
        successfully delivered - data is only correctly stored in a buffer to
        be sent. There's no guarantee it is truly delivered.

        :param headers: A list of key-value tuples as headers.
        :param end_stream: To send a request without body, set `end_stream` to
                           `True` (default `False`).
        :return: Stream ID as a integer, used for further communication.
        """
        yield from _wait_for_events(self._resumed, self._stream_creatable)
        stream_id = self._conn.get_next_available_stream_id()
        self._priority.insert_stream(stream_id)
        self._priority.block(stream_id)
        self._conn.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()
        return stream_id

    @asyncio.coroutine
    def start_response(self, stream_id, headers, *, end_stream=False):
        """
        Start a response by sending given headers on the given stream.

        This may block until the underlying transport becomes writable.

        :param stream_id: Which stream to send response on.
        :param headers: A list of key-value tuples as headers.
        :param end_stream: To send a response without body, set `end_stream` to
                           `True` (default `False`).
        """
        yield from self._resumed.wait()
        self._conn.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()

    @asyncio.coroutine
    def send_data(self, stream_id, data, *, end_stream=False):
        """
        Send request or response body on the given stream.

        This will block until either whole data is sent, or the stream gets
        closed. Meanwhile, a paused underlying transport or a closed flow
        control window will also help waiting. If the peer increase the flow
        control window, this method will start sending automatically.

        This can be called multiple times, but it must be called after a
        `start_request` or `start_response` with the returning stream ID, and
        before any `end_stream` instructions; Otherwise it will fail.

        The given data may be automatically split into smaller frames in order
        to fit in the configured frame size or flow control window.

        Each stream can only have one `send_data` running, others calling this
        will be blocked on a per-stream lock (wlock), so that coroutines
        sending data concurrently won't mess up with each other.

        Similarly, the completion of the call to this method does not mean the
        data is delivered.

        :param stream_id: Which stream to send data on
        :param data: Bytes to send
        :param end_stream: To finish sending a request or response, set this to
                           `True` to close the given stream locally after data
                           is sent (default `False`).
        :raise: `SendException` if there is an error sending data. Data left
                unsent can be found in `data` of the exception.
        """
        try:
            with (yield from self._get_stream(stream_id).wlock):
                while True:
                    yield from _wait_for_events(
                        self._resumed, self._get_stream(stream_id).window_open)
                    self._priority.unblock(stream_id)
                    waiter = asyncio.Future()
                    if not self._priority_events:
                        self._loop.call_soon(self._priority_step)
                    self._priority_events[stream_id] = waiter
                    try:
                        yield from waiter
                        data_size = len(data)
                        size = min(
                            data_size,
                            self._conn.local_flow_control_window(stream_id),
                            self._conn.max_outbound_frame_size)
                        if data_size == 0 or size == data_size:
                            self._conn.send_data(stream_id, data,
                                                 end_stream=end_stream)
                            self._flush()
                            break
                        elif size > 0:
                            self._conn.send_data(stream_id, data[:size])
                            data = data[size:]
                            self._flush()
                    finally:
                        self._priority_events.pop(stream_id, None)
                        self._priority.block(stream_id)
                        if self._priority_events:
                            self._loop.call_soon(self._priority_step)
        except ProtocolError:
            raise exceptions.SendException(data)

    @asyncio.coroutine
    def send_trailers(self, stream_id, headers):
        """
        Send trailers on the given stream, closing the stream locally.

        This may block until the underlying transport becomes writable, or
        other coroutines release the wlock on this stream.

        :param stream_id: Which stream to send trailers on.
        :param headers: A list of key-value tuples as trailers.
        """
        with (yield from self._get_stream(stream_id).wlock):
            yield from self._resumed.wait()
            self._conn.send_headers(stream_id, headers, end_stream=True)
            self._flush()

    @asyncio.coroutine
    def end_stream(self, stream_id):
        """
        Close the given stream locally.

        This may block until the underlying transport becomes writable, or
        other coroutines release the wlock on this stream.

        :param stream_id: Which stream to close.
        """
        with (yield from self._get_stream(stream_id).wlock):
            yield from self._resumed.wait()
            self._conn.end_stream(stream_id)
            self._flush()

    @asyncio.coroutine
    def recv_request(self):
        """
        Retrieve next inbound request in queue.

        This will block until a request is available.

        :return: A tuple `(stream_id, headers)`.
        """
        rv = yield from self._inbound_requests.get()
        return rv[1:]

    @asyncio.coroutine
    def recv_response(self, stream_id):
        """
        Wait until a response is ready on the given stream.

        :param stream_id: Stream to wait on.
        :return: A list of key-value tuples as response headers.
        """
        return (yield from self._get_stream(stream_id).response)

    @asyncio.coroutine
    def recv_trailers(self, stream_id):
        """
        Wait until trailers are ready on the given stream.

        :param stream_id: Stream to wait on.
        :return: A list of key-value tuples as trailers.
        """
        return (yield from self._get_stream(stream_id).trailers)

    @asyncio.coroutine
    def read_stream(self, stream_id, size=None):
        """
        Read data from the given stream.

        By default (`size=None`), this returns all data left in current HTTP/2
        frame. In other words, default behavior is to receive frame by frame.

        If size is given a number above zero, method will try to return as much
        bytes as possible up to the given size, block until enough bytes are
        ready or stream is remotely closed.

        If below zero, it will read until the stream is remotely closed and
        return everything at hand.

        `size=0` is a special case that does nothing but returns `b''`. The
        same result `b''` is also returned under other conditions if there is
        no more data on the stream to receive, even under `size=None` and peer
        sends an empty frame - you can use `b''` to safely identify the end of
        the given stream.

        Flow control frames will be automatically sent while reading clears the
        buffer, allowing more data to come in.

        :param stream_id: Stream to read
        :param size: Expected size to read, `-1` for all, default frame.
        :return: Bytes read or empty if there is no more to expect.
        """
        rv = []
        try:
            with (yield from self._get_stream(stream_id).rlock):
                if size is None:
                    rv.append((
                        yield from self._get_stream(stream_id).read_frame()))
                    self._flow_control(stream_id)
                elif size < 0:
                    while True:
                        rv.extend((
                            yield from self._get_stream(stream_id).read_all()))
                        self._flow_control(stream_id)
                else:
                    while size > 0:
                        bufs, count = yield from self._get_stream(
                            stream_id).read(size)
                        rv.extend(bufs)
                        size -= count
                        self._flow_control(stream_id)
        except StreamClosedError:
            pass
        except _StreamEndedException as e:
            try:
                self._flow_control(stream_id)
            except StreamClosedError:
                pass
            rv.extend(e.bufs)
        return b''.join(rv)

    def update_settings(self, new_settings):
        self._conn.update_settings(new_settings)
        self._flush()

    @asyncio.coroutine
    def wait_functional(self):
        """
        Wait until the connection becomes functional.

        The connection is count functional if it was active within last few
        seconds (defined by `functional_timeout`), where a newly-made
        connection and received data indicate activeness.

        :return: Most recently calculated round-trip time if any.
        """
        while not self._is_functional():
            self._rtt = None
            self._ping_index += 1
            self._ping_time = self._loop.time()
            self._conn.ping(struct.pack('Q', self._ping_index))
            self._flush()
            try:
                yield from asyncio.wait_for(self._functional.wait(),
                                            self._functional_timeout)
            except asyncio.TimeoutError:
                pass
        return self._rtt

    def reprioritize(self, stream_id,
                     depends_on=None, weight=16, exclusive=False):
        """
        Update the priority status of an existing stream.

        :param stream_id: The stream ID of the stream being updated.
        :param depends_on: (optional) The ID of the stream that the stream now
            depends on. If ``None``, will be moved to depend on stream 0.
        :param weight: (optional) The new weight to give the stream. Defaults
            to 16.
        :param exclusive: (optional) Whether this stream should now be an
            exclusive dependency of the new parent.
        """
        self._priority.reprioritize(stream_id, depends_on, weight, exclusive)

    @property
    def functional_timeout(self):
        """
        A timeout value in seconds used by `wait_functional`, beyond which will
        self send a PING frame since last activeness and block the call to
        `wait_functional` until acknowledgement is received.

        Setting this to a larger value may cause pending `wait_functional`
        calls to unblock immediately.
        """
        return self._functional_timeout

    @functional_timeout.setter
    def functional_timeout(self, val):
        self._functional_timeout = val
        self._functional.sync()

    @property
    def initial_window_size(self):
        """
        Self initial window size (in octets) for stream-level flow control.

        Setting a larger value may cause the inbound buffer increase, and allow
        more data to be received. Setting with a smaller value does not
        decrease the buffer immediately, but may prevent the peer from sending
        more data to overflow the buffer for a while. However, it is still up
        to the peer whether to respect this setting or not.
        """
        return self._conn.local_settings.initial_window_size

    @initial_window_size.setter
    def initial_window_size(self, val):
        self._conn.update_settings({settings.SettingCodes.INITIAL_WINDOW_SIZE: val})
        self._flush()

    @property
    def max_frame_size(self):
        """
        The size of the largest frame payload that self is willing to receive,
        in octets.

        Smaller value indicates finer data slices the peer should send, vice
        versa; the peer however may not agree.
        """
        return self._conn.local_settings.max_frame_size

    @max_frame_size.setter
    def max_frame_size(self, val):
        self._conn.update_settings({settings.SettingCodes.MAX_FRAME_SIZE: val})
        self._flush()

    @property
    def max_concurrent_streams(self):
        """
        The maximum number of concurrent streams that self is willing to allow.
        """
        return self._conn.local_settings.max_concurrent_streams

    @max_concurrent_streams.setter
    def max_concurrent_streams(self, val):
        self._conn.update_settings({settings.SettingCodes.MAX_CONCURRENT_STREAMS: val})
        self._flush()

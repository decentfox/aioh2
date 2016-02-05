import socket

import asyncio

from .protocol import H2Protocol

__all__ = ['open_connection', 'start_server']
if hasattr(socket, 'AF_UNIX'):
    __all__.extend(['open_unix_connection', 'start_unix_server'])


@asyncio.coroutine
def open_connection(host=None, port=None, *, loop=None, **kwargs):
    if loop is None:
        loop = asyncio.get_event_loop()
    rv = H2Protocol(True, loop=loop)
    # noinspection PyArgumentList
    yield from loop.create_connection(lambda: rv, host, port, **kwargs)
    return rv


@asyncio.coroutine
def start_server(client_connected_cb, host=None, port=None, *, loop=None,
                 **kwargs):
    if loop is None:
        loop = asyncio.get_event_loop()

    def factory():
        rv = H2Protocol(False, loop=loop)
        client_connected_cb(rv)
        return rv

    # noinspection PyArgumentList
    return (yield from loop.create_server(factory, host, port, **kwargs))


if hasattr(socket, 'AF_UNIX'):
    @asyncio.coroutine
    def open_unix_connection(path=None, *, loop=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        rv = H2Protocol(True, loop=loop)
        # noinspection PyArgumentList
        yield from loop.create_unix_connection(lambda: rv, path, **kwargs)
        return rv


    @asyncio.coroutine
    def start_unix_server(client_connected_cb, path=None, *, loop=None,
                          **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()

        def factory():
            rv = H2Protocol(False, loop=loop)
            client_connected_cb(rv)
            return rv

        # noinspection PyArgumentList
        return (yield from loop.create_unix_server(factory, path, **kwargs))



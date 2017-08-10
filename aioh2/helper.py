import socket

import asyncio

from .protocol import H2Protocol

__all__ = ['open_connection', 'start_server']
if hasattr(socket, 'AF_UNIX'):
    __all__.extend(['open_unix_connection', 'start_unix_server'])

PROTOCOL_KWARGS = ('concurrency', 'functional_timeout')


class _None:
    pass


def _split_kwargs(kwargs):
    rv = {}
    for key in PROTOCOL_KWARGS:
        val = kwargs.pop(key, _None)
        if val is not _None:
            rv[key] = val
    return rv


@asyncio.coroutine
def open_connection(host=None, port=None, *, loop=None, **kwargs):
    if loop is None:
        loop = asyncio.get_event_loop()
    # noinspection PyArgumentList
    rv = H2Protocol(True, loop=loop, **_split_kwargs(kwargs))
    # noinspection PyArgumentList
    yield from loop.create_connection(lambda: rv, host, port, **kwargs)
    return rv


@asyncio.coroutine
def start_server(client_connected_cb, host=None, port=None, *, loop=None,
                 **kwargs):
    if loop is None:
        loop = asyncio.get_event_loop()

    args = _split_kwargs(kwargs)

    def factory():
        # noinspection PyArgumentList
        rv = H2Protocol(False, loop=loop, **args)
        rv.set_handler(client_connected_cb(rv))
        return rv

    # noinspection PyArgumentList
    return (yield from loop.create_server(factory, host, port, **kwargs))


if hasattr(socket, 'AF_UNIX'):
    @asyncio.coroutine
    def open_unix_connection(path=None, *, loop=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        # noinspection PyArgumentList
        rv = H2Protocol(True, loop=loop, **_split_kwargs(kwargs))
        # noinspection PyArgumentList
        yield from loop.create_unix_connection(lambda: rv, path, **kwargs)
        return rv

    @asyncio.coroutine
    def start_unix_server(client_connected_cb, path=None, *, loop=None,
                          **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()

        args = _split_kwargs(kwargs)

        def factory():
            # noinspection PyArgumentList
            rv = H2Protocol(False, loop=loop, **args)
            rv.set_handler(client_connected_cb(rv))
            return rv

        # noinspection PyArgumentList
        return (yield from loop.create_unix_server(factory, path, **kwargs))


if hasattr(asyncio, 'ensure_future'):  # Python >= 3.5
    async_task = asyncio.ensure_future
else:
    async_task = asyncio.async

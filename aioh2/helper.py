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


async def open_connection(host=None, port=None, *, cls=H2Protocol, loop=None,
                          **kwargs):
    if loop is None:
        loop = asyncio.get_running_loop()
    # noinspection PyArgumentList
    protocol = cls(True, **_split_kwargs(kwargs))
    # noinspection PyArgumentList
    await loop.create_connection(lambda: protocol, host, port, **kwargs)
    return protocol


async def start_server(client_connected_cb, host=None, port=None, *, cls=H2Protocol,
                       loop=None, **kwargs):
    if loop is None:
        loop = asyncio.get_running_loop()

    args = _split_kwargs(kwargs)

    def factory():
        # noinspection PyArgumentList
        protocol = cls(False, **args)
        protocol.set_handler(client_connected_cb(protocol))
        return protocol

    # noinspection PyArgumentList
    return await loop.create_server(factory, host, port, **kwargs)


if hasattr(socket, 'AF_UNIX'):

    async def open_unix_connection(path=None, *, loop=None, **kwargs):
        if loop is None:
            loop = asyncio.get_running_loop()
        # noinspection PyArgumentList
        protocol = H2Protocol(True, **_split_kwargs(kwargs))
        # noinspection PyArgumentList
        await loop.create_unix_connection(lambda: protocol, path, **kwargs)
        return protocol

    async def start_unix_server(client_connected_cb, path=None, *, loop=None,
                                **kwargs):
        if loop is None:
            loop = asyncio.get_running_loop()

        args = _split_kwargs(kwargs)

        def factory():
            # noinspection PyArgumentList
            protocol = H2Protocol(False, **args)
            protocol.set_handler(client_connected_cb(protocol))
            return protocol

        # noinspection PyArgumentList
        return await loop.create_unix_server(factory, path, **kwargs)

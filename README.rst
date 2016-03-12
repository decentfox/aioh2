=====
aioh2
=====

.. image:: https://img.shields.io/pypi/v/aioh2.svg
        :target: https://pypi.python.org/pypi/aioh2

.. image:: https://img.shields.io/travis/decentfox/aioh2.svg
        :target: https://travis-ci.org/decentfox/aioh2

.. image:: https://readthedocs.org/projects/aioh2/badge/?version=latest
        :target: https://readthedocs.org/projects/aioh2/?badge=latest
        :alt: Documentation Status


HTTP/2 implementation with hyper-h2_ on Python 3 asyncio.

* Free software: BSD license
* Documentation: https://aioh2.readthedocs.org.

Features
--------

* Asynchronous HTTP/2 client and server
* Multiplexing streams of data with managed flow and priority control
* Optional tickless health check
* More to come

Non-features:

* Request/Response wrappers
* Web server, dispatcher, cookie, etc
* HTTP/2 upgrade

Example
-------

A server saying hello:

.. code-block:: python

    # Server request handler
    async def on_connected(proto):
        try:
            while True:
                # Receive a request from queue
                stream_id, headers = await proto.recv_request()

                # Send response headers
                await proto.start_response(stream_id, {':status': '200'})

                # Response body starts with "hello"
                await proto.send_data(stream_id, b'hello, ')

                # Read all request body as a name from client side
                name = await proto.read_stream(stream_id, -1)

                # Amend response body with the name
                await proto.send_data(stream_id, name)

                # Send trailers with the length of the name
                await proto.send_trailers(stream_id, {'len': str(len(name))})
        except asyncio.CancelledError:
            # do cleanup here
            pass

    # Start server on random port, with maximum concurrent requests of 3
    server = await aioh2.start_server(on_connected, port=0, concurrency=3)
    port = server.sockets[0].getsockname()[1]


And a client to try out:

.. code-block:: python

    # Open client connection
    client = await aioh2.open_connection('0.0.0.0', port,
                                         functional_timeout=0.1)

    # Optionally wait for an ack of tickless ping - a.k.a. until functional
    await asyncio.sleep(0.1)  # simulate client being busy with something else
    rtt = await client.wait_functional()
    if rtt:
        print('Round-trip time: %.1fms' % (rtt * 1000))

    # Start request with headers
    stream_id = await client.start_request(
        {':method': 'GET', ':path': '/index.html'})

    # Send my name "world" as whole request body
    await client.send_data(stream_id, b'world', end_stream=True)

    # Receive response headers
    headers = await client.recv_response(stream_id)
    print('Response headers:', headers)

    # Read all response body
    resp = await client.read_stream(stream_id, -1)
    print('Response body:', resp)

    # Read response trailers
    trailers = await client.recv_trailers(stream_id)
    print('Response trailers:', trailers)

    client.close_connection()
    await asyncio.sleep(.1)


Above example can be found at `examples/core.py`.


Credits
-------

A big thanks to the great library hyper-h2_ from `Cory Benfield`_.

`DecentFoX Studio`_ is a software outsourcing company delivering high-quality
web-based products and mobile apps for global customers with agile methodology,
focusing on bleeding-edge technologies and fast-developing scalable architectures.

This package was created with Cookiecutter_ and the `audreyr/cookiecutter-pypackage`_ project template.

.. _Cookiecutter: https://github.com/audreyr/cookiecutter
.. _`audreyr/cookiecutter-pypackage`: https://github.com/audreyr/cookiecutter-pypackage
.. _hyper-h2: https://github.com/python-hyper/hyper-h2
.. _`DecentFoX Studio`: http://decentfox.com
.. _`Cory Benfield`: https://github.com/Lukasa

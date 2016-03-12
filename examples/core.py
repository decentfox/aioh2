import asyncio
import aioh2

async def example():
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
            print('Task terminated, saving to disk...')

    # Start server on random port, with maximum concurrent requests of 3
    server = await aioh2.start_server(on_connected, port=0, concurrency=3)
    port = server.sockets[0].getsockname()[1]

    # Open client connection
    client = await aioh2.open_connection('0.0.0.0', port,
                                         functional_timeout=0.1)

    # Optionally wait for an ack of tickless ping - a.k.a. until functional
    await asyncio.sleep(0.1)  # simulate a busy client with something else
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


asyncio.get_event_loop().run_until_complete(example())

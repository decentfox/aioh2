import asyncio
import aioh2

async def example():
    # Server request handler
    async def on_connected(proto):
        while True:
            # Receive request
            stream_id, headers = await proto.recv_request()

            # Send response headers
            await proto.start_response(stream_id, {':status': '200'})

            # Send some response
            await proto.send_data(stream_id, b'hello, ')

            # Read all request body
            resp = await proto.read_stream(stream_id, -1)

            # Send more response
            await proto.send_data(stream_id, resp)

            # Send trailers
            await proto.send_trailers(stream_id, {'len': str(len(resp))})

    # Start server on random port, with maximum concurrent requests of 3
    server = await aioh2.start_server(
        lambda p: asyncio.get_event_loop().create_task(on_connected(p)),
        port=0, concurrency=3)
    port = server.sockets[0].getsockname()[1]

    # Open client connection
    client = await aioh2.open_connection('0.0.0.0', port)

    # Start request with headers
    stream_id = await client.start_request(
        {':method': 'GET', ':path': '/index.html'})

    # Send request body
    await client.send_data(stream_id, b'world', end_stream=True)

    # Receive response
    headers = await client.recv_response(stream_id)
    print(headers)

    # Read all response body
    resp = await client.read_stream(stream_id, -1)
    print(resp)

    # Read response trailers
    trailers = await client.recv_trailers(stream_id)
    print(trailers)

asyncio.get_event_loop().run_until_complete(example())


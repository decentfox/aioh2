import functools

import asyncio


def async_test(timeout=1):
    func = None
    if callable(timeout):
        func = timeout
        timeout = 1

    def _decorator(f):
        @functools.wraps(f)
        def _wrapper(self, *args, **kwargs):
            return self.loop.run_until_complete(
                asyncio.wait_for(
                    asyncio.coroutine(f)(self, *args, **kwargs), timeout,
                    loop=self.loop))

        return _wrapper

    if func is not None:
        return _decorator(func)

    return _decorator

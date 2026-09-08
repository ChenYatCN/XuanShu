"""Run parallel workers without leaving siblings alive after failure or cancellation."""
import asyncio


async def gather_owned(*coroutines, return_exceptions=False):
    tasks = [asyncio.ensure_future(coro) for coro in coroutines]
    try:
        return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

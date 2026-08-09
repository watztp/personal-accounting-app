from __future__ import annotations

import asyncio
import logging

from category_helper.worker import CategoryWatcher


async def _run() -> None:
    watcher = CategoryWatcher()
    await watcher.start()
    try:
        await asyncio.Event().wait()
    finally:
        await watcher.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

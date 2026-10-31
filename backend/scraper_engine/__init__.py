import asyncio
import sys
import concurrent.futures
from typing import List, Dict, Any, Optional, Callable
from .router import ScraperRouter
from .models import ProductData

def _run_async_in_isolation(coro_func, *args, **kwargs):
    """
    Runs an async function in a dedicated thread with a fresh ProactorEventLoop on Windows,
    completely isolating Playwright subprocesses from Uvicorn's event loop.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args, **kwargs))
    finally:
        loop.close()

def scrape_urls_to_json(
    urls: List[str], progress_callback: Optional[Callable[[str], Any]] = None
) -> List[Dict[str, Any]]:
    """
    Synchronous entrypoint for your pipeline:
    Pass 800-1000 URLs -> Returns full structured JSON.
    """
    router = ScraperRouter()
    
    # If called from a sync context or FastAPI background task, run in isolated thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _run_async_in_isolation,
            router.scrape_batch,
            urls,
            progress_callback
        )
        return future.result()

def scrape_urls_for_excel(
    urls: List[str], progress_callback: Optional[Callable[[str], Any]] = None
) -> List[Dict[str, Any]]:
    """
    Synchronous entrypoint for your Excel generator:
    Pass 800-1000 URLs -> Returns flattened dictionary rows ready for Pandas/Excel.
    """
    raw_results = scrape_urls_to_json(urls, progress_callback=progress_callback)
    return [ProductData(**item).to_excel_row() for item in raw_results]
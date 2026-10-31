import asyncio
import inspect
import logging
import random
import sys
import time
from typing import List, Dict, Any, Optional, Callable
from curl_cffi.requests import AsyncSession

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

from .config import ScraperConfig
from .models import ProductData
from .parsers import ParsingLayer

logger = logging.getLogger("ScraperEngine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BLOCK_SIGNATURES = [
    "access denied",
    "robot or human",
    "verify you are human",
    "pardon our interruption",
    "px-captcha",
    "challenge-platform",
    "automated access to amazon data",
    "reference #",
]

FAST_READY_SELECTORS = (
    "script[type='application/ld+json'], "
    "script#__NEXT_DATA__, "
    "#productTitle, "
    "h1"
)

class ScraperRouter:
    def __init__(self):
        self.http_sem = asyncio.Semaphore(ScraperConfig.HTTP_CONCURRENCY)
        self.browser_sem = asyncio.Semaphore(ScraperConfig.BROWSER_CONCURRENCY)

    @staticmethod
    def _is_blocked_or_empty(html: str) -> bool:
        if not html or len(html) < 3500:
            return True
        lower_sample = html[:15000].lower()
        if "application/ld+json" in lower_sample or "__next_data__" in lower_sample:
            return False
        return any(sig in lower_sample for sig in BLOCK_SIGNATURES)

    async def _fetch_http(self, url: str, shared_session: AsyncSession) -> Optional[str]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        async with self.http_sem:
            for attempt in range(1, ScraperConfig.MAX_RETRIES + 1):
                proxy = ScraperConfig.get_rotating_proxy()
                try:
                    resp = await shared_session.get(
                        url,
                        headers=headers,
                        proxy=proxy,
                        timeout=ScraperConfig.HTTP_TIMEOUT,
                        allow_redirects=True,
                    )
                    if resp.status_code == 200 and not self._is_blocked_or_empty(resp.text):
                        return resp.text
                    if resp.status_code in (403, 429, 503) and not proxy:
                        break
                except Exception as e:
                    logger.debug(f"[HTTP Attempt {attempt}] {url} failed: {e}")
                    if not proxy:
                        break
                await asyncio.sleep(random.uniform(0.1, 0.3))
        return None

    async def _fetch_browser(self, url: str, browser) -> Optional[str]:
        async with self.browser_sem:
            for attempt in range(1, ScraperConfig.MAX_RETRIES + 1):
                proxy_cfg = ScraperConfig.get_sticky_proxy()
                context = None
                try:
                    context = await browser.new_context(
                        proxy=proxy_cfg,
                        ignore_https_errors=True,
                        viewport={"width": 1366, "height": 768},
                        locale="en-US",
                        timezone_id="America/New_York",
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    )

                    await context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = { runtime: {} };
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    """)

                    page = await context.new_page()

                    await page.route(
                        "**/*",
                        lambda route: route.abort()
                        if route.request.resource_type in ("image", "media", "font", "stylesheet")
                        or any(
                            tracker in route.request.url
                            for tracker in (
                                "google-analytics",
                                "googletagmanager",
                                "doubleclick",
                                "bazaarvoice",
                                "optimizely",
                                "facebook.net",
                                "criteo",
                            )
                        )
                        else route.continue_(),
                    )

                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=ScraperConfig.BROWSER_TIMEOUT * 1000,
                    )

                    try:
                        await page.wait_for_selector(
                            FAST_READY_SELECTORS, state="attached", timeout=3500
                        )
                    except Exception:
                        pass

                    html = await page.content()
                    if not self._is_blocked_or_empty(html):
                        return html
                except Exception as e:
                    logger.debug(f"[Browser Attempt {attempt}] {url} failed: {e}")
                finally:
                    if context:
                        await context.close()
        return None

    async def scrape_single_url(self, url: str, shared_session: AsyncSession, browser) -> ProductData:
        domain = ScraperConfig.extract_base_domain(url)
        engine_used = "http"

        if domain not in ScraperConfig.BROWSER_FIRST_DOMAINS:
            html = await self._fetch_http(url, shared_session)
            if html:
                parsed = ParsingLayer.parse(html, url, engine_used="http")
                if parsed.title and (parsed.price is not None or domain == "costco.com"):
                    return parsed

        engine_used = "browser"
        html = await self._fetch_browser(url, browser)
        if html:
            return ParsingLayer.parse(html, url, engine_used=engine_used)

        return ProductData(
            url=url,
            merchant=domain,
            status="failed",
            engine_used=engine_used,
            error_message="Blocked by WAF or timeout",
        )

    async def scrape_batch(
        self, urls: List[str], progress_callback: Optional[Callable[[str], Any]] = None
    ) -> List[Dict[str, Any]]:
        # Fix Windows SelectorEventLoop NotImplementedError for subprocesses
        if sys.platform == "win32":
            try:
                loop = asyncio.get_running_loop()
                if not isinstance(loop, asyncio.ProactorEventLoop):
                    logger.warning("Switching to WindowsProactorEventLoopPolicy for Playwright support.")
            except RuntimeError:
                pass
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        ScraperConfig.initialize_proxyscrape_api()
        start_t = time.perf_counter()

        logger.info(f"Starting batch scrape for {len(urls)} URLs...")
        
        # Helper to handle both sync and async callbacks safely without RuntimeWarnings
        async def safe_callback(msg: str):
            if progress_callback:
                try:
                    if inspect.iscoroutinefunction(progress_callback):
                        await progress_callback(msg)
                    else:
                        progress_callback(msg)
                except Exception as e:
                    logger.debug(f"Progress callback error: {e}")

        await safe_callback(f"Starting batch scrape for {len(urls)} URLs...")

        async with AsyncSession(impersonate="chrome124", verify=False, max_clients=50) as shared_session:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                )

                async def wrapped_task(url: str):
                    res = await self.scrape_single_url(url, shared_session, browser)
                    status_emoji = "✅" if res.status == "success" else "❌"
                    await safe_callback(f"{status_emoji} Scraped [{res.engine_used}]: {url} ({res.status})")
                    return res

                tasks = [wrapped_task(url) for url in urls]
                results: List[ProductData] = await asyncio.gather(*tasks)
                await browser.close()

        elapsed = time.perf_counter() - start_t
        success_count = sum(1 for r in results if r.status == "success")
        summary_msg = f"Batch completed in {elapsed:.2f}s: {success_count}/{len(urls)} succeeded."
        logger.info(summary_msg)
        await safe_callback(summary_msg)

        return [r.model_dump() for r in results]
import os
import random
import uuid
import logging
from typing import Optional, List, Dict
from urllib.parse import urlparse
from curl_cffi import requests as cffi_requests

logger = logging.getLogger("ScraperEngine")

class ScraperConfig:
    # 1. ProxyScrape v4 Account API Key
    PS_API_KEY_NAME: str = os.getenv("PROXYSCRAPE_API_KEY_NAME", "Kkey")
    PS_API_KEY: str = os.getenv(
        "PROXYSCRAPE_API_KEY",
        "r0NmOs78dI8GTMrZ2Pg3yUmFHvjKZmfkIZBOVE4xP7nEgJF6dx1uDdgaN1TSeXyj",
    )

    # 2. ProxyScrape Residential Gateway (from Dashboard -> Residential -> Proxy Setup)
    # Leave blank during local dev to use Direct Fast Mode (avoids slow dead public proxies)
    PS_RESIDENTIAL_USER: str = os.getenv("PROXYSCRAPE_RES_USER", "")
    PS_RESIDENTIAL_PASS: str = os.getenv("PROXYSCRAPE_RES_PASS", "")
    PS_RESIDENTIAL_HOST: str = os.getenv("PROXYSCRAPE_RES_HOST", "rp.scrapegw.com")
    PS_RESIDENTIAL_PORT: str = os.getenv("PROXYSCRAPE_RES_PORT", "6060")
    PS_COUNTRY: str = os.getenv("PROXYSCRAPE_COUNTRY", "us")

    # Set USE_FREE_PROXIES=true ONLY if you explicitly want to test free public proxies
    USE_FREE_PROXIES: bool = os.getenv("USE_FREE_PROXIES", "false").lower() == "true"

    # 3. High-Speed Concurrency & Fail-Fast Timeouts
    HTTP_CONCURRENCY: int = int(os.getenv("HTTP_CONCURRENCY", "30"))
    BROWSER_CONCURRENCY: int = int(os.getenv("BROWSER_CONCURRENCY", "12"))
    HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "8"))         # Fast 8s timeout for HTTP
    BROWSER_TIMEOUT: int = int(os.getenv("BROWSER_TIMEOUT", "15"))  # Fast 15s timeout for Browser
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))

    _api_proxy_pool: List[str] = []

    # Domains routed directly to Browser Automation Engine first (Strict Akamai/Cloudflare)
    BROWSER_FIRST_DOMAINS = {
        "bestbuy.com",
        "lowes.com",
        "costco.com",
        "menards.com",
    }

    # All 11 supported target merchants
    SUPPORTED_MERCHANTS = {
        "acehardware.com",
        "amazon.com",
        "bestbuy.com",
        "costco.com",
        "harborfreight.com",
        "homedepot.com",
        "lowes.com",
        "menards.com",
        "tractorsupply.com",
        "walmart.com",
        "northerntool.com",
    }

    @classmethod
    def has_residential_proxy(cls) -> bool:
        return bool(cls.PS_RESIDENTIAL_USER and cls.PS_RESIDENTIAL_PASS)

    @classmethod
    def initialize_proxyscrape_api(cls) -> None:
        """
        Verifies ProxyScrape API key and only loads free proxies if USE_FREE_PROXIES=true.
        """
        if cls.has_residential_proxy():
            logger.info("ProxyScrape Residential Gateway configured and active.")
            return

        if not cls.USE_FREE_PROXIES:
            logger.info("Direct Fast Mode active (Residential proxy not set; skipping slow free proxies).")
            return

        headers = {"api-token": cls.PS_API_KEY, "Accept": "application/json"}
        try:
            proxy_list_url = (
                f"https://api.proxyscrape.com/v4/free-proxy-list/get"
                f"?request=display_proxies&protocol=http&country={cls.PS_COUNTRY},ca"
                f"&ssl=yes&anonymity=elite,anonymous&timeout=2500&proxy_format=protocolipport&format=text"
            )
            r = cffi_requests.get(proxy_list_url, headers=headers, timeout=8)
            if r.status_code == 200 and r.text.strip():
                cls._api_proxy_pool = [
                    line.strip()
                    for line in r.text.splitlines()
                    if line.strip().startswith("http")
                ]
                logger.info(f"Loaded {len(cls._api_proxy_pool)} free proxies from ProxyScrape API.")
        except Exception as e:
            logger.warning(f"Failed to load free proxy list from ProxyScrape API: {e}")

    @classmethod
    def get_rotating_proxy(cls) -> Optional[str]:
        """Returns per-request rotating proxy for HTTP Client Engine."""
        if cls.has_residential_proxy():
            user_formatted = f"{cls.PS_RESIDENTIAL_USER}-country-{cls.PS_COUNTRY}"
            return f"http://{user_formatted}:{cls.PS_RESIDENTIAL_PASS}@{cls.PS_RESIDENTIAL_HOST}:{cls.PS_RESIDENTIAL_PORT}"

        if cls.USE_FREE_PROXIES and cls._api_proxy_pool:
            return random.choice(cls._api_proxy_pool)

        return None

    @classmethod
    def get_sticky_proxy(cls) -> Optional[Dict[str, str]]:
        """Returns sticky session proxy dict for Playwright/Patchright Browser Engine."""
        if cls.has_residential_proxy():
            session_id = uuid.uuid4().hex[:10]
            sticky_username = (
                f"{cls.PS_RESIDENTIAL_USER}-country-{cls.PS_COUNTRY}"
                f"-os-windows-session-{session_id}-lifetime-5"
            )
            return {
                "server": f"http://{cls.PS_RESIDENTIAL_HOST}:{cls.PS_RESIDENTIAL_PORT}",
                "username": sticky_username,
                "password": cls.PS_RESIDENTIAL_PASS,
            }

        if cls.USE_FREE_PROXIES and cls._api_proxy_pool:
            return {"server": random.choice(cls._api_proxy_pool)}

        return None

    @staticmethod
    def extract_base_domain(url: str) -> str:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
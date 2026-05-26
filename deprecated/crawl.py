import asyncio
import json
import hashlib
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

OUT_DIR = Path("crawl_output")
OUT_DIR.mkdir(exist_ok=True)

SKIP_PATHS = ["/cart", "/privacy", "/terms"]

def normalize_url(url: str) -> str:
    parsed = urlparse(url)

    # remove trailing slash completely
    path = parsed.path.rstrip("/")

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))

def filename_for_url(url: str) -> str:
    url = normalize_url(url)
    digest = hashlib.md5(url.encode()).hexdigest()[:10]

    readable = (
        url.replace("https://", "")
           .replace("http://", "")
           .replace("/", "_")
    )

    return f"{digest}_{readable}.json"

async def main():
    run_config = CrawlerRunConfig(
        word_count_threshold=20,
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=2,
            max_pages=10,
            include_external=False
        )
    )

    seen_urls = set()

    async with AsyncWebCrawler(config=BrowserConfig(verbose=True)) as crawler:
        results = await crawler.arun(
            url="https://www.psiquantum.com",
            config=run_config
        )

        for result in results: # type: ignore
            url = normalize_url(result.url)

            if url in seen_urls:
                print("Duplicate skipped:", url)
                continue

            seen_urls.add(url)

            if any(skip in url.lower() for skip in SKIP_PATHS):
                print("Skipped:", url)
                continue

            path = OUT_DIR / filename_for_url(url)

            path.write_text(json.dumps({
                "url": url,
                "markdown": str(result.markdown),
                "html": result.html
            }, indent=2), encoding="utf-8")

            print("Saved/updated:", path)

if __name__ == "__main__":
    asyncio.run(main())
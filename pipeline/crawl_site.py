#!/usr/bin/env python3
"""Crawl a website and save each page as JSON in crawl_output/.

Usage:
    python3 crawl_site.py https://example.com --crawl 2 --max-pages 10
"""
import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

OUT_DIR = Path("crawl_output")
SKIP_PATHS = ["/cart", "/privacy", "/terms"]


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
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


async def crawl(seed_url: str, max_depth: int, max_pages: int) -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
        print(f"Cleared existing {OUT_DIR}/")
    OUT_DIR.mkdir(exist_ok=True)

    run_config = CrawlerRunConfig(
        word_count_threshold=20,
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            include_external=False,
        ),
    )

    seen_urls = set()

    async with AsyncWebCrawler(config=BrowserConfig(verbose=True)) as crawler:
        results = await crawler.arun(url=seed_url, config=run_config)

        for result in results:  # type: ignore
            url = normalize_url(result.url)

            if url in seen_urls:
                print("Duplicate skipped:", url)
                continue
            seen_urls.add(url)

            if any(skip in url.lower() for skip in SKIP_PATHS):
                print("Skipped:", url)
                continue

            path = OUT_DIR / filename_for_url(url)
            path.write_text(
                json.dumps({
                    "url": url,
                    "markdown": str(result.markdown),
                    "html": result.html,
                }, indent=2),
                encoding="utf-8",
            )
            print("Saved:", path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Seed URL to crawl")
    parser.add_argument(
        "--crawl", "-c", type=int, default=3,
        help="Max crawl depth (default 3)",
    )
    parser.add_argument(
        "--max-pages", type=int, default=20,
        help="Max pages to crawl (default 20)",
    )
    args = parser.parse_args()

    asyncio.run(crawl(args.url, args.crawl, args.max_pages))


if __name__ == "__main__":
    main()

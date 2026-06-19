import json
import re
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, unquote


SITEMAPS = [
    "https://thequantuminsider.com/post-sitemap.xml",
    "https://thequantuminsider.com/post-sitemap2.xml",
    "https://thequantuminsider.com/post-sitemap3.xml",
    "https://thequantuminsider.com/post-sitemap4.xml",
    "https://thequantuminsider.com/post-sitemap5.xml",
    "https://thequantuminsider.com/post-sitemap6.xml",
    "https://thequantuminsider.com/post-sitemap7.xml",
    "https://thequantuminsider.com/post-sitemap8.xml",
    "https://www.insidequantumtechnology.com/news-sitemap.xml",
    "https://www.insidequantumtechnology.com/news-sitemap2.xml",
    "https://www.insidequantumtechnology.com/news-sitemap3.xml",
    "https://www.insidequantumtechnology.com/news-sitemap4.xml",
    "https://www.insidequantumtechnology.com/news-sitemap5.xml",
    "https://www.insidequantumtechnology.com/news-sitemap6.xml",
    "https://www.insidequantumtechnology.com/news-sitemap7.xml",
    "https://www.insidequantumtechnology.com/news-sitemap8.xml",
    "https://www.insidequantumtechnology.com/news-sitemap9.xml",
    "https://www.insidequantumtechnology.com/news-sitemap10.xml"
]

OUTPUT_FILE = "news_with_date.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; QuantumResearchBot/1.0)"
}


def fetch_xml(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            print(f"Failed {url}: {response.status_code}")
            return None

        return response.text

    except requests.RequestException as e:
        print(f"Request failed for {url}: {e}")
        return None


def get_text(element, tag_ending):
    """
    Namespace-agnostic XML tag lookup.
    Works for tags like:
    loc
    lastmod
    news:title
    news:publication_date
    """

    for child in element.iter():
        if child.tag.lower().endswith(tag_ending.lower()):
            if child.text:
                return child.text.strip()

    return None


def normalize_sitemap_date(date_text):
    """
    Extract YYYY-MM-DD from sitemap dates like:
    2022-01-15T10:30:00+00:00
    """

    if not date_text:
        return None

    match = re.match(r"(\d{4}-\d{2}-\d{2})", date_text)

    if match:
        return match.group(1)

    return None


def date_from_url(url):
    """
    Fallback for URLs like:
    https://thequantuminsider.com/2019/12/03/article-title/
    """

    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)

    if not match:
        return None

    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def get_article_date(url_element, loc):
    """
    Prefer sitemap publication metadata.
    Fall back to lastmod.
    Fall back to date from URL.
    """

    return (
        normalize_sitemap_date(get_text(url_element, "publication_date"))
        or normalize_sitemap_date(get_text(url_element, "lastmod"))
        or date_from_url(loc)
    )


def title_from_url(url):
    """
    Generate a readable title from the URL slug.
    """

    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1]

    slug = unquote(slug)

    slug = re.sub(r"\.html?$", "", slug)
    slug = slug.replace("-", " ")
    slug = slug.replace("_", " ")
    slug = re.sub(r"\s+", " ", slug).strip()

    return slug.title()


def make_article_key(title, url, published_date=None):
    """
    Creates a unique readable key.
    Includes the date if available.
    """

    short_hash = hashlib.md5(
        url.encode("utf-8")
    ).hexdigest()[:8]

    clean_title = re.sub(
        r"\s+",
        " ",
        title
    ).strip()

    if published_date:
        return f"{published_date} - {clean_title} [{short_hash}]"

    return f"{clean_title} [{short_hash}]"


def parse_sitemap(xml_text):
    root = ET.fromstring(xml_text)

    articles = []

    for url_element in root.iter():

        if not url_element.tag.lower().endswith("url"):
            continue

        loc = get_text(url_element, "loc")

        if not loc:
            continue

        title = (
            get_text(url_element, "title")
            or title_from_url(loc)
        )

        published_date = get_article_date(url_element, loc)

        articles.append({
            "title": title,
            "url": loc,
            "published_date": published_date
        })

    return articles


def main():

    all_articles = {}

    for sitemap_url in SITEMAPS:

        print(f"Reading sitemap: {sitemap_url}")

        xml_text = fetch_xml(sitemap_url)

        if not xml_text:
            continue

        try:
            articles = parse_sitemap(xml_text)

        except Exception as e:
            print(f"Could not parse {sitemap_url}: {e}")
            continue

        print(f"Found {len(articles)} articles")

        for article in articles:

            key = make_article_key(
                article["title"],
                article["url"],
                article.get("published_date")
            )

            all_articles[key] = {
                "website_link": article["url"]
            }

        time.sleep(1)

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            all_articles,
            f,
            indent=4,
            ensure_ascii=False
        )

    print()
    print(f"Saved {len(all_articles)} articles to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
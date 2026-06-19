import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


ARTICLE_URL = "https://thequantuminsider.com/2025/09/23/top-quantum-computing-companies/"
OUTPUT_FILE = "quantum_companies.json"


def is_company_website_link(href: str) -> bool:
    if not href:
        return False

    blocked_domains = [
        "thequantuminsider.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "youtube.com",
        "instagram.com",
        "airmeet.com",
    ]

    parsed = urlparse(href)
    domain = parsed.netloc.lower().replace("www.", "")

    if not domain:
        return False

    return not any(blocked in domain for blocked in blocked_domains)


def clean_company_name(heading_text: str) -> str | None:
    match = re.match(r"^\s*\d+\.\s+(.+)", heading_text)

    if not match:
        return None

    return match.group(1).strip().title()


def find_company_website_after_heading(heading, company_name: str) -> str | None:
    clean_name = re.sub(r"\(.*?\)", "", company_name).strip().lower()

    for sibling in heading.find_all_next():

        if sibling.name in ["h2", "h3", "h4"] and sibling != heading:
            break

        links = []

        if sibling.name == "a" and sibling.get("href"):
            links.append(sibling)

        links.extend(sibling.find_all("a", href=True))

        # Prefer links whose text matches the company name
        for a in links:
            link_text = a.get_text(" ", strip=True).lower()
            href = a.get("href")

            if clean_name in link_text and is_company_website_link(href):
                return href

        # Fallback: first external company website
        for a in links:
            href = a.get("href")

            if is_company_website_link(href):
                return href

    return None


def scrape_quantum_companies() -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        ARTICLE_URL,
        headers=headers,
        timeout=20
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    companies = {}

    headings = soup.find_all(["h2", "h3", "h4"])

    for heading in headings:

        heading_text = heading.get_text(" ", strip=True)

        company_name = clean_company_name(heading_text)

        if not company_name:
            continue

        website_link = find_company_website_after_heading(
            heading,
            company_name
        )

        if not website_link:
            continue

        companies[company_name] = {
            "website_link": website_link
        }

    return companies


def save_to_json(data: dict, output_file: str) -> None:
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )


if __name__ == "__main__":
    companies = scrape_quantum_companies()

    save_to_json(companies, OUTPUT_FILE)

    print(f"Saved {len(companies)} companies to {OUTPUT_FILE}")
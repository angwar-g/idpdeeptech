import json
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


INPUT_JSON = "invalid_companies.json"
OUTPUT_FIXED_JSON = "invalid_companies_fixed.json"
OUTPUT_REVIEW_JSON = "invalid_companies_needing_review.json"

MAX_WORKERS = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def normalize_candidates(url):
    url = str(url).strip()

    if not url:
        return []

    parsed = urlparse(url)

    if parsed.scheme:
        base = url
    else:
        base = "https://" + url

    parsed = urlparse(base)
    domain = parsed.netloc
    path = parsed.path or ""

    candidates = []

    if domain.startswith("www."):
        bare_domain = domain.replace("www.", "", 1)
        www_domain = domain
    else:
        bare_domain = domain
        www_domain = "www." + domain

    for scheme in ["https", "http"]:
        candidates.append(f"{scheme}://{bare_domain}{path}")
        candidates.append(f"{scheme}://{www_domain}{path}")

    return list(dict.fromkeys(candidates))


def check_url(url):
    try:
        response = requests.head(
            url,
            headers=HEADERS,
            allow_redirects=True,
            timeout=15
        )

        if response.status_code in [403, 405]:
            response = requests.get(
                url,
                headers=HEADERS,
                allow_redirects=True,
                timeout=15
            )

        return {
            "url": url,
            "valid": response.status_code < 400,
            "status_code": response.status_code,
            "final_url": response.url
        }

    except requests.RequestException as e:
        return {
            "url": url,
            "valid": False,
            "status_code": None,
            "final_url": None,
            "error": str(e)
        }


def find_working_url(original_url):
    for candidate in normalize_candidates(original_url):
        result = check_url(candidate)

        if result["valid"]:
            return result

        # Some sites block bots but are probably real
        if result["status_code"] in [401, 403, 406, 429]:
            result["possibly_valid"] = True
            return result

    return {
        "url": original_url,
        "valid": False,
        "possibly_valid": False
    }


def process_company(company_name, company_data):
    original_url = company_data.get("website_link")

    if not original_url:
        return company_name, company_data, {
            "status": "missing_url"
        }

    result = find_working_url(original_url)

    updated_data = dict(company_data)

    if result.get("valid"):
        updated_data["website_link"] = result["final_url"]
        return company_name, updated_data, {
            "status": "fixed",
            "original_url": original_url,
            "working_url": result["final_url"],
            "status_code": result["status_code"]
        }

    if result.get("possibly_valid"):
        updated_data["website_link"] = result["url"]
        return company_name, updated_data, {
            "status": "possibly_valid",
            "original_url": original_url,
            "candidate_url": result["url"],
            "status_code": result["status_code"]
        }

    return company_name, updated_data, {
        "status": "needs_manual_review",
        "original_url": original_url
    }


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)

    fixed_companies = {}
    review_needed = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_company, name, data)
            for name, data in companies.items()
        ]

        for i, future in enumerate(as_completed(futures), start=1):
            company_name, updated_data, report = future.result()

            fixed_companies[company_name] = updated_data

            print(f"[{i}/{len(companies)}] {company_name}: {report['status']}")

            if report["status"] in {
                "possibly_valid",
                "needs_manual_review",
                "missing_url"
            }:
                review_needed[company_name] = report

    with open(OUTPUT_FIXED_JSON, "w", encoding="utf-8") as f:
        json.dump(fixed_companies, f, indent=4, ensure_ascii=False)

    with open(OUTPUT_REVIEW_JSON, "w", encoding="utf-8") as f:
        json.dump(review_needed, f, indent=4, ensure_ascii=False)

    print()
    print(f"Saved fixed company JSON to {OUTPUT_FIXED_JSON}")
    print(f"Saved review list to {OUTPUT_REVIEW_JSON}")


if __name__ == "__main__":
    main()
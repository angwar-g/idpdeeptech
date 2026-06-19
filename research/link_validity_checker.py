import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


INPUT_JSON = "news.json"
OUTPUT_JSON = "news_validation_results.json"

MAX_WORKERS = 20


def verify_url(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.head(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=10
        )

        if response.status_code in [403, 405]:
            response = requests.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=10
            )

        return {
            "valid": response.status_code < 400,
            "status_code": response.status_code,
            "final_url": response.url
        }

    except requests.RequestException as e:
        return {
            "valid": False,
            "status_code": None,
            "final_url": None,
            "error": str(e)
        }


def validate_company(company_name: str, company_data: dict) -> tuple:
    website_link = company_data.get("website_link")

    if not website_link:
        website_result = {
            "valid": False,
            "error": "Missing website link"
        }
    else:
        website_result = verify_url(website_link)

    return company_name, website_result


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)

    invalid_results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(validate_company, company_name, company_data)
            for company_name, company_data in companies.items()
        ]

        for future in as_completed(futures):
            company_name, result = future.result()

            website_valid = result["valid"]

            print(
                f"{company_name}\n"
                f"  Website: {'VALID' if website_valid else 'INVALID'}\n"
            )

            # Only save invalid website links
            if not website_valid:
                invalid_results[company_name] = {
                    "website": result
                }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(invalid_results, f, indent=4)

    print(f"\nInvalid website links saved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
import json


ORIGINAL_JSON = "philipp_companies.json"
REVIEW_JSON = "quantum_companies.json"
OUTPUT_JSON = "philipp_companies_clean.json"


def normalize_url(url):
    if not url:
        return None

    return str(url).strip().lower().rstrip("/")


def main():

    with open(ORIGINAL_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)

    with open(REVIEW_JSON, "r", encoding="utf-8") as f:
        review_data = json.load(f)

    review_urls = set()

    for _, info in review_data.items():

        # Try all common fields that might contain the URL
        possible_urls = [
            info.get("original_url"),
            info.get("candidate_url"),
            info.get("website_link"),
        ]

        for url in possible_urls:
            normalized = normalize_url(url)

            if normalized:
                review_urls.add(normalized)

    cleaned_companies = {}

    removed = 0

    for company_name, company_data in companies.items():

        company_url = normalize_url(
            company_data.get("website_link")
        )

        if company_url in review_urls:
            removed += 1
            continue

        cleaned_companies[company_name] = company_data

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            cleaned_companies,
            f,
            indent=4,
            ensure_ascii=False
        )

    print(f"Original companies : {len(companies)}")
    print(f"Removed companies  : {removed}")
    print(f"Remaining companies: {len(cleaned_companies)}")
    print(f"Saved to: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
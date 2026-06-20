import json


ORIGINAL_JSON = "philipp_companies.json"
REVIEW_JSON = "invalid_companies_fixed.json"
OUTPUT_JSON = "philipp_companies_clean.json"


def main():
    with open(ORIGINAL_JSON, "r", encoding="utf-8") as f:
        companies = json.load(f)

    with open(REVIEW_JSON, "r", encoding="utf-8") as f:
        review_companies = json.load(f)

    review_names = set(review_companies.keys())

    cleaned_companies = {
        company_name: company_data
        for company_name, company_data in companies.items()
        if company_name not in review_names
    }

    removed = len(companies) - len(cleaned_companies)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            cleaned_companies,
            f,
            indent=4,
            ensure_ascii=False
        )

    print(f"Original companies: {len(companies)}")
    print(f"Removed companies: {removed}")
    print(f"Remaining companies: {len(cleaned_companies)}")
    print(f"Saved to: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
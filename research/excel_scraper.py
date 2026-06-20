import json
import pandas as pd


INPUT_EXCEL = "Philipp_Company_Websites.xlsx"
OUTPUT_JSON = "philipp_companies.json"

COMPANY_COLUMN = "Company name (final)"
WEBSITE_COLUMN = "Website (final)"


def clean_text(value):
    if pd.isna(value):
        return None

    value = str(value).strip()

    if not value:
        return None

    return value


def clean_website(value):
    if pd.isna(value):
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.lower() in {"nan", "n/a", "none", "-"}:
        return None

    if not value.startswith(("http://", "https://")):
        value = "https://" + value

    return value


def main():
    df = pd.read_excel(INPUT_EXCEL)

    for column in [COMPANY_COLUMN, WEBSITE_COLUMN]:
        if column not in df.columns:
            raise ValueError(
                f"Missing required column: {column}\n"
                f"Available columns: {list(df.columns)}"
            )

    companies = {}

    for _, row in df.iterrows():
        company_name = clean_text(row[COMPANY_COLUMN])
        website_link = clean_website(row[WEBSITE_COLUMN])

        if not company_name or not website_link:
            continue

        companies[company_name] = {
            "website_link": website_link
        }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            companies,
            f,
            indent=4,
            ensure_ascii=False
        )

    print(f"Saved {len(companies)} companies to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
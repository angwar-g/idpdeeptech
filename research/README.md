# Quantum News and Company Scraping Utilities

This folder contains Python scripts for collecting quantum news articles and quantum company website links, and for checking whether extracted links are valid.

## Files

### `quantum_news_sitemap_scrape.py`

Scrapes article URLs from [The Quantum Insider](https://thequantuminsider.com/sitemap_index.xml) and [Inside Quantum Technology](https://www.insidequantumtechnology.com/sitemap.xml) sitemaps and saves them to `news.json`.

Each article is stored as a JSON entry where the key is a readable article title plus a short hash, and the value contains the article URL under `website_link`.

**Output:**

```text
news.json
```

**Run:**

```bash
python quantum_news_sitemap_scrape.py
```

---

### `sitemap_scrape_with_date.py`

Scrapes article URLs from The Quantum Insider and Inside Quantum Technology sitemaps, extracts the article date from sitemap metadata where available, and saves the results to `news_with_date.json`.

The date is added to the article name in the JSON key, for example:

```text
2024-05-10 - Example Quantum News Article [abc12345]
```

The script first tries to read date information from the sitemap metadata, such as `publication_date` or `lastmod`, and falls back to extracting a date from the URL when needed.

**Output:**

```text
news_with_date.json
```

**Run:**

```bash
python sitemap_scrape_with_date.py
```

---

### `top_quantum_companies_extractor.py`

Scrapes The Quantum Insider article listing top quantum computing companies and extracts company names together with their website links.

The extracted companies are saved in JSON format, with each company name as a key and its website link as the value.

**Output:**

```text
quantum_companies.json
```

**Run:**

```bash
python top_quantum_companies_extractor.py
```

---

### `link_validity_checker.py`

Checks whether the website links in a JSON file are valid. The script reads links from `news.json`, sends HTTP requests to each link, and saves only the invalid links to a separate JSON file.

By default, it reads from:

```text
news.json
```

and writes invalid results to:

```text
news_validation_results.json
```

**Run:**

```bash
python link_validity_checker.py
```

If you want to validate a different JSON file, edit these variables inside the script:

```python
INPUT_JSON = "news.json"
OUTPUT_JSON = "news_validation_results.json"
```

For example, to validate `quantum_companies.json`, change them to:

```python
INPUT_JSON = "quantum_companies.json"
OUTPUT_JSON = "company_validation_results.json"
```

---

## Requirements

Install the required Python packages before running the scripts:

```bash
pip install requests beautifulsoup4
```

The sitemap scripts only require `requests` and Python standard-library modules. The company extractor also requires `beautifulsoup4`.

---

## Date Disclaimer

The news data was scraped on **19-06-2026**.

For JSON files where dates appear in the article names, the date should be interpreted as the article's **last modified date**, as read from the sitemap metadata. It should not necessarily be treated as the original publication date.

For example, an entry like:

```text
2024-05-10 - Example Quantum News Article [abc12345]
```

means that the sitemap reported `2024-05-10` as the article's relevant date, typically from the sitemap `lastmod` field. This may reflect when the article was last updated rather than when it was first published.

---

## Notes

- The scripts use a custom `User-Agent` header to make requests more explicit.
- The sitemap scripts pause briefly between sitemap requests to avoid sending too many requests too quickly.
- Article keys include short hashes to reduce the chance of duplicate titles overwriting each other.
- The link checker saves only invalid links, so an empty output JSON means no invalid links were found.

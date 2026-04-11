# Third-Party Dependencies

All direct runtime dependencies used by Job Search Agent, with installed versions and licence types.
Versions reflect the current development environment — pin to these in `requirements.txt` for reproducible builds.

## Direct Dependencies

| Package | Version | Licence | Purpose |
|---|---|---|---|
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | 0.89.0 | Apache 2.0 | Official Anthropic SDK — all Claude API calls |
| [pydantic](https://docs.pydantic.dev) | 2.12.5 | MIT | Data validation for all Claude responses and config |
| [PyYAML](https://pyyaml.org) | 6.0.3 | MIT | Parses `config/config.yaml` |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2.2 | BSD 3-Clause | Loads `.env` file (API keys) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD 3-Clause | HTTP client for scrapers and API calls |
| [feedparser](https://feedparser.readthedocs.io) | 6.0.12 | BSD 2-Clause | Parses RSS/Atom feeds from job sources |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | 0.11.9 | MIT | Extracts text from `resume.pdf` |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup) | 4.14.3 | MIT | Parses HTML from scraped job pages |
| [tenacity](https://tenacity.readthedocs.io) | 9.1.4 | Apache 2.0 | Retry with exponential backoff on API calls |
| [rich](https://rich.readthedocs.io) | 14.3.3 | MIT | Terminal formatting for run output and tables |
| [streamlit](https://streamlit.io) | 1.56.0 | Apache 2.0 | Browser dashboard (`dashboard.py`) |
| [pandas](https://pandas.pydata.org) | 3.0.2 | BSD 3-Clause | DataFrame operations in the dashboard |
| [plotly](https://plotly.com/python) | 6.6.0 | MIT | Bar charts in the Companies dashboard view |
| [pytest](https://docs.pytest.org) | 9.0.3 | MIT | Test framework — `python -m pytest tests/` |

## Licence Summary

| Licence | Packages | Key obligations |
|---|---|---|
| **Apache 2.0** | anthropic, tenacity, streamlit | Retain licence + NOTICE, patent grant included |
| **MIT** | pydantic, PyYAML, pdfplumber, beautifulsoup4, rich, plotly | Retain copyright notice |
| **BSD 3-Clause** | python-dotenv, httpx, pandas | Retain copyright notice, no endorsement of derivatives |
| **BSD 2-Clause** | feedparser | Retain copyright notice |

All licences are permissive — none impose copyleft requirements on your own code.

## Compatibility with Apache 2.0

This project is licensed under Apache 2.0. All dependencies listed above use permissive licences (Apache 2.0, MIT, BSD) that are compatible with Apache 2.0 distribution.

## Python Standard Library

This project also uses the following standard library modules, which require no third-party attribution:
`argparse`, `collections`, `datetime`, `json`, `logging`, `math`, `os`, `pathlib`, `sqlite3`, `subprocess`, `sys`

## Keeping This Up to Date

To regenerate installed versions:

```bash
pip show anthropic pydantic pyyaml python-dotenv httpx feedparser pdfplumber beautifulsoup4 tenacity rich streamlit pandas plotly pytest | grep -E "^(Name|Version):"
```

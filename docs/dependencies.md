# Third-Party Dependencies

All direct runtime dependencies used by Job Search Agent v2, with installed versions and license types.
Versions reflect the current development environment — pin to these in `requirements.txt` for reproducible builds.

---

## v2 Dependencies (new in refactor)

| Package | Version | Licence | Purpose |
|---|---|---|---|
| [langchain-core](https://python.langchain.com) | 1.3.2 | MIT | Message / prompt-template types used by `PromptLoader`. (The `langchain` meta-package is not imported directly.) |
| [langchain-anthropic](https://github.com/langchain-ai/langchain-anthropic) | 1.4.2 | MIT | LangChain integration for Claude via `ChatAnthropic` |
| [openai](https://github.com/openai/openai-python) | 2.30.0 | Apache 2.0 | Official OpenAI SDK — used directly by the `OpenAIProvider` (ADR-053 multi-provider); OpenAI models register only when `OPENAI_API_KEY` is set |
| [tiktoken](https://github.com/openai/tiktoken) | 0.8.0 | MIT | Token counting for the `OpenAIProvider`'s cost/usage estimation |
| [langgraph](https://langchain-ai.github.io/langgraph) | 1.1.4 | MIT | Stateful workflow graph orchestration + HITL |
| [langgraph-checkpoint-sqlite](https://github.com/langchain-ai/langgraph) | 3.0.3 | MIT | `SqliteSaver` — persists LangGraph checkpoints to SQLite |
| [fastapi](https://fastapi.tiangolo.com) | 0.136.1 | MIT | REST API backend — workflow endpoints |
| [uvicorn](https://www.uvicorn.org) | 0.45.0 | BSD 3-Clause | ASGI server for FastAPI |
| [starlette](https://www.starlette.io) | 1.0.0 | BSD 3-Clause | ASGI toolkit — pulled in by FastAPI; `TestClient` used in tests |
| [pdfminer.six](https://github.com/pdfminer/pdfminer.six) | 20251230 | MIT | Direct pdfminer access for the v2 `ResumeParser` — better multi-column layout handling than pdfplumber |
| [python-docx](https://python-docx.readthedocs.io) | 1.2.0 | MIT | Word (`.docx`) export of the Resume Clinic's final resume (ADR-066) — pure Python, no system deps |
| [reportlab](https://www.reportlab.com/opensource/) | 4.5.1 | BSD 3-Clause | PDF export of the Resume Clinic's final resume (ADR-066) — pure Python, no system deps |

---

## v1 Dependencies (shared with v2)

| Package | Version | Licence | Purpose |
|---|---|---|---|
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | 0.96.0 | Apache 2.0 | Official Anthropic SDK — all Claude API calls |
| [pydantic](https://docs.pydantic.dev) | 2.11.10 | MIT | Data validation for all agent outputs and config |
| [PyYAML](https://pyyaml.org) | 6.0.3 | MIT | Parses `config/config.yaml` |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.1.1 | BSD 3-Clause | Loads `.env` file (API keys) |
| [httpx](https://www.python-httpx.org) | 0.28.1 | BSD 3-Clause | HTTP client for scrapers and API calls |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup) | 4.13.5 | MIT | Parses HTML from scraped job pages |
| [tenacity](https://tenacity.readthedocs.io) | 9.1.4 | Apache 2.0 | Retry with exponential backoff on API calls |
| [streamlit](https://streamlit.io) | 1.56.0 | Apache 2.0 | Browser UI (`app/ui/streamlit_app.py`) |
| [pandas](https://pandas.pydata.org) | 2.3.3 | BSD 3-Clause | DataFrame operations in the UI |
| [plotly](https://plotly.com/python) | 6.7.0 | MIT | Bar charts in the UI dashboard view |

---

## Test Dependencies

| Package | Version | Licence | Purpose |
|---|---|---|---|
| [pytest](https://docs.pytest.org) | 9.0.3 | MIT | Test framework — `python -m pytest tests/` |

> `pytest-asyncio` and `pytest-mock` are listed in `requirements.txt` but installed into the system environment.
> `httpx` (above) also provides `TestClient` support for FastAPI integration tests via Starlette.

---

## Licence Summary

| Licence | Packages | Key obligations |
|---|---|---|
| **Apache 2.0** | anthropic, openai, tenacity, streamlit | Retain licence + NOTICE, patent grant included |
| **MIT** | pydantic, PyYAML, pdfminer.six, python-docx, beautifulsoup4, plotly, tiktoken, langchain-core, langchain-anthropic, langgraph, langgraph-checkpoint-sqlite, fastapi | Retain copyright notice |
| **BSD 3-Clause** | python-dotenv, httpx, pandas, uvicorn, starlette, reportlab | Retain copyright notice, no endorsement of derivatives |

All licences are permissive — none impose copyleft requirements on your own code.

---

## Compatibility with Apache 2.0

This project is licensed under Apache 2.0. All dependencies listed above use permissive licences (Apache 2.0, MIT, BSD) that are compatible with Apache 2.0 distribution.

---

## Python Standard Library

This project uses the following standard library modules, which require no third-party attribution:

`argparse`, `builtins`, `collections`, `concurrent.futures`, `contextlib`, `dataclasses`, `datetime`, `hashlib`, `json`, `logging`, `math`, `os`, `pathlib`, `sqlite3`, `subprocess`, `symtable`, `sys`, `threading`, `typing`, `uuid`

---

## Keeping This Up to Date

To check installed versions:

```bash
pip show anthropic openai tiktoken pydantic pyyaml python-dotenv httpx pdfminer.six python-docx reportlab beautifulsoup4 tenacity streamlit pandas plotly pytest langchain-core langchain-anthropic langgraph langgraph-checkpoint-sqlite fastapi uvicorn starlette | grep -E "^(Name|Version):"
```

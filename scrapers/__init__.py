# scrapers/__init__.py
# Retained as shared libraries used by v2 (ADR-063): ConcurrentAdzunaScraper wraps
# AdzunaScraper and dependencies.py builds LinkedInScraper. The Ladders scraper and
# the v1 entry points were removed.
from scrapers.linkedin import LinkedInScraper
from scrapers.adzuna import AdzunaScraper

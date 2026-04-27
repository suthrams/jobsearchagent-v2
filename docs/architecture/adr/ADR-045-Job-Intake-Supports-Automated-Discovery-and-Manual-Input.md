# ADR-045: Job Intake Supports Automated Discovery and Manual Input

## Status
Accepted

## Context
v1 already supports job discovery through scrapers/APIs. Requiring users to provide individual job URLs in v2 would reduce usability and remove an important working capability from v1.

## Decision
v2 will support automated job discovery as the primary intake path, while also allowing manual job URLs and pasted job descriptions as optional inputs.

## Rationale
Automated discovery reduces user friction and preserves the value already present in v1. Manual input remains useful for specific jobs, blocked pages, or roles found outside supported sources.

## Consequences
### Positive
- Preserves v1 functionality
- Reduces user friction
- Supports batch scoring
- Allows fallback for blocked scraping

### Tradeoffs
- Requires job source abstraction
- Requires normalization across different job sources
- Increases need for deduplication and source tracking

## Implementation Notes
- Create a JobDiscoveryService
- Keep source-specific scrapers behind a common interface
- Normalize all jobs into a common JobPosting schema
- Allow manual URL and pasted JD as fallback paths
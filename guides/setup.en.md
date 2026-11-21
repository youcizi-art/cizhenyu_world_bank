# World Bank Data setup

## No API key required

World Bank Indicators API v2 is open — install, enable, and call immediately.

- Docs: [Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation)
- Base: `https://api.worldbank.org/v2/`

## Verify

- Agent: `wb_health_check`
- CLI: `python server.py check`

## Quick codes

- Countries: `CHN` / `USA` (ISO3; multiple with `;`)
- Common indicators: GDP, population, inflation — use `wb_search_indicators` to look up IDs.

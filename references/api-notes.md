# API Notes for Event Finder

## Timepad API

**Base URL:** `https://api.timepad.ru/v1/events.json`

**Auth:** OAuth 2.0 required for most endpoints. Public events may work without token but often return 403.
**Docs:** https://dev.timepad.ru/api/get-v1-events

**Key parameters:**
- `keywords` — array of words to match in event name
- `cities` — array of city names (Russian)
- `starts_at_min` / `starts_at_max` — ISO 8601 date range
- `limit` (1-100), `skip` — pagination
- `fields` — extra fields: location, registration_data, categories, ticket_types
- `moderation_statuses` — featured, shown, not_moderated
- `price_max` — max ticket price (0 for free)

**Note:** Timepad API requires OAuth registration at dev.timepad.ru for reliable access.

## KudaGo API

**Base URL:** `https://kudago.com/public-api/v1.4/events/`

**Auth:** None required (free public API)
**Docs:** https://docs.kudago.com/api/

**Key parameters:**
- `text` — full-text search
- `location` — city slug: msk, spb, ekb, nsk, nnv, kzn, smr, krd, sochi, ufa, krasnoyarsk, vbg, kev, new-york
- `actual_only=true` — only active events (but data may be stale)
- `page_size` (max 100), `page` — pagination
- `order_by` — `-start_date`, `publication_date`
- `is_free=true` — free events only
- `fields` — id,title,description,price,site_url,place,dates,categories,age_restriction,is_free

**Important:** KudaGo `actual_only` doesn't reliably filter by date. Client-side filtering by `dates[0].start` timestamp is required. The API data may be stale — events from years ago may still appear.

## Rate Limits
- Timepad: undocumented, cache for 30 min
- KudaGo: generous, cache for 30 min

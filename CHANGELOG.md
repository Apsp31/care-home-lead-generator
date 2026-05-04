# Changelog

All notable changes to the Care Home Lead Generator are listed here.
Versions are auto-incremented on each commit (major.minor).

---

## v1.35 — 2026-05-04

### Added
- Persistent database support via PostgreSQL: set `DATABASE_URL` env var / Streamlit secret to point at a Supabase (or any Postgres) database and all data — users, searches, leads — persists across Streamlit Cloud restarts
- `db/schema.py` now contains a `_Conn` wrapper that normalises SQLite vs PostgreSQL differences (`?`→`%s`, `:name`→`%(name)s`, `AUTOINCREMENT`→`SERIAL`, `datetime('now')`→`NOW()`, `lastrowid` via `RETURNING id`, `executescript` splitting); no changes needed in the rest of the codebase
- `psycopg2-binary` added to requirements

### Fixed
- `INSERT OR IGNORE` in `upsert_organisation` replaced with explicit `ON CONFLICT(source, source_id) DO NOTHING` (works in both SQLite 3.24+ and PostgreSQL)
- `auth.py` register check used fragile `fetchone()[0]`; now uses named column `cnt`

---

## v1.34 — 2026-05-04

### Added
- New **Retirement Villages** data source (`sources/retirement_villages.py`) — searches for UK retirement villages near the target location using DuckDuckGo, targeting 13 major operators (McCarthy & Stone, Churchill, Inspired Villages, Audley, Richmond, Rangeford, Birchgrove, ExtraCare, Pegasus Life, Housing 21, Anchor, Retirement Villages Group, Homewise) plus generic local queries
- New `retirement_village` org type with base score 0.65 and wealth indicator 0.80 (residents are owner-occupiers who funded village entry; high self-funder rate when transitioning to residential care)
- "Retirement Villages (UK operators)" source option in New Search; "Retirement villages" category in org type picker

### Fixed
- Retirement villages source: tightened noise filtering — added 30+ noise domains (Glassdoor, Cylex, LinkedIn Jobs, OpenRent, Autumna, etc.), rejected non-UK TLDs (.com.au, .hk), added "retirement" qualifier to all provider-specific queries, tightened village keyword matching to title/URL rather than body text, provider-domain matching for borderline results

---

## v1.31 — 2026-03-16

### Fixed
- Sidebar API key captions (Companies House, Google Places, CQC) now only shown when the key is absent
- CQC source now distinguishes a missing key (registration prompt) from a rejected/invalid key (HTTP 401/403 → "check key is correct and subscription is active")

---

## v1.29 — 2026-03-16

### Fixed
- CQC source updated for new API endpoint (`api.service.cqc.org.uk`): list endpoint now returns only id/name/postcode; now paginates list → bulk geocode → fetch detail per in-range location; registrationStatus, address, phone from detail response
- CQC local authority lookup now uses `admin_county` (e.g. Hertfordshire) not `admin_district` (e.g. Watford) — county-level matches CQC's LA filter
- `bulk_geocode_postcodes` moved to `geocoder.py` and shared between CQC and NHS ODS sources

---

## v1.27 — 2026-03-16

### Added
- "First seen this run" toggle on Lead Dashboard — filters to orgs that have never appeared in a prior run for the same care home (uses `get_repeat_org_ids`)

---

## v1.25 — 2026-03-16

### Added
- Admin: Danger Zone — clear all data button (requires checkbox confirmation); preserves users and sessions

---

## v1.24 — 2026-03-16

### Fixed
- OSM node/way duplicates removed: hospital dedup by name+proximity (<1km) in `_fetch_hospitals`; non-hospital dedup by name+type+proximity (<0.3km) in `_batch_query`
- DB migration on startup removes ~60 existing duplicate leads from prior runs

---

## v1.23 — 2026-03-16

### Fixed
- Overpass: `address_line1` now combines `addr:housenumber` + `addr:street` (was missing house numbers)
- Companies House: `address_line1` now combines `premises` + `address_line_1`
- CQC source: added `Ocp-Apim-Subscription-Key` authentication header; `CQC_API_KEY` env var required

---

## v1.22 — 2026-03-16

### Fixed
- NHS ODS GP surgeries now correctly geocoded: switched from paginating 12,000+ orgs to querying by outward code (geographic pre-filter via postcodes.io `/outcodes/nearest`)
- Address extracted from `Addresses` array (not `GeoLoc.Location` which holds only address fields, not lat/lon)
- Contacts parsed correctly from `{"Contact": [...]}` dict structure
- Terminated postcodes handled via `/terminated_postcodes/{pc}` fallback in bulk geocoder
- Removed invalid `Offset=0` parameter (caused 406 from ODS API)

---

## v1.18 — 2026-03-14

### Fixed
- SOLLA firms now geocode correctly: secondary DDG address search run when no postcode found in snippet; out-of-area firms rejected via haversine radius guard
- US/international LinkedIn contacts filtered from web/social and SOLLA results (`_US_LOCATION_RE` applied consistently)
- Duplicate org entries (e.g. "Age UK Barnet | Age UK") deduplicated — `_clean_title` strips everything after `|`; `_add()` dedup key normalised the same way
- Out-of-area community orgs (e.g. "Age UK Solihull" in a London search) rejected via Nominatim place-name geocoding on org name
- Wrong-county hospital LinkedIn contacts filtered via employer area geocoding check; `radius_km` threaded through `_linkedin_profiles` call sites

### Added
- **Re-run Search** button on Lead Dashboard — pre-fills New Search form with exact settings (care home, postcode, radius, sources, org categories, hospital depts) from the selected run, enabling a clean data refresh
- `geocode_place()` in `sources/geocoder.py` — Nominatim place-name geocoding, cached, rate-limited to 1 req/sec (OSM policy)

---

## v1.17 — 2026-03-14

### Added
- Background threading for searches — UI remains navigable while search runs; sidebar progress widget polls every 2 s via `@st.fragment(run_every=2)`

---

## v1.16 — 2026-03-14

### Added
- Instructions page: step-by-step guide, source table, scoring table, tips including tab-close warning

---

## v1.15 — 2026-03-14

### Added
- Hospital contact enrichment (`sources/hospital_enrichment.py`): PALS contact per hospital (always); trust website dept pages + NHS Jobs postings (full enrichment opt-in)

---

## v1.14 — 2026-03-14

### Added
- Postcode geocoding for web/social/SOLLA results — extracts UK postcode from DDG snippet body and resolves to lat/lon via postcodes.io

---

## v1.13 — 2026-03-14

### Fixed
- Overpass OSM results now geocode via `addr:postcode` tag when element has no native lat/lon; non-zero distances computed for previously unlocatable orgs

---

## v1.12 — 2026-03-14

### Fixed
- GP surgeries geocoded via postcode fallback when ODS API omits GeoLoc coordinates

---

## v1.11 — 2026-03-14

### Added
- Domiciliary care agencies and care placement advisers as lead categories (sources, scoring, map colours, legend)
- Admin: promote/revoke admin status for other users

---

## v1.10 — 2026-03-14

### Added
- Logged-in username displayed in sidebar

---

## v1.9 — 2026-03-14

### Added
- User management: self-registration, PBKDF2-SHA256 password hashing, session tokens stored in URL query param for persistence across browser sessions
- Auth gate: all pages require login; first registered user becomes admin
- Admin page: user list with promote/revoke, all-searches table with lead counts

---

## v1.8 — 2026-03-14

### Added
- Landing page with login / register tabs
- Instructions page

---

## v1.7 — 2026-03-13

### Added
- SOLLA source (`sources/solla.py`): finds SOLLA-accredited care fees IFAs via web + LinkedIn DDG searches
- Map View: click marker → full lead card shown below map; batch contact pre-fetch

---

## v1.6 — 2026-03-13

### Added
- LinkedIn enrichment (`sources/enrichment.py`): adds named contacts for orgs with none
- Scoring Weights page

---

## v1.5 — 2026-03-13

### Added
- Feedback / CRM page: status tracking, notes, carry-forward across re-runs (scoped to care home)
- Map View page with Folium, radius rings, high-contrast colour palette

---

## v1.4 — 2026-03-13

### Added
- Lead Dashboard with org-type grouping, score filter, HTML report export, re-score button
- Hospital department leads (7 types) from Overpass

---

## v1.3 — 2026-03-13

### Added
- Companies House source: solicitors, IFAs, wealth managers, estate agents
- Web/Social source: dementia cafes, Age UK, carers groups, day centres, LinkedIn/Facebook

---

## v1.2 — 2026-03-13

### Added
- NHS ODS source: GP surgeries, NHS trusts, PCNs
- Overpass/OSM source: hospices, pharmacies, community orgs

---

## v1.1 — 2026-03-13

### Added
- Initial Streamlit app with New Search page, SQLite DB, scoring engine

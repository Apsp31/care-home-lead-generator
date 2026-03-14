# Changelog

All notable changes to the Care Home Lead Generator are listed here.
Versions are auto-incremented on each commit (major.minor).

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

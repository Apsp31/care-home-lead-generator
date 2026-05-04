# Care Home Lead Generator

A Streamlit app for care home referral outreach. Given a postcode and search radius it finds organisations likely to refer self-funding residents, scores them by priority, and tracks your outreach activity.

## What it does

- **Finds** GP surgeries, hospital departments, solicitors, estate agents, dementia cafes, carers groups and more — from NHS, OpenStreetMap, Companies House and web/social search
- **Scores** each lead by referral priority, wealth indicator (likelihood of self-funding clients), distance, and data completeness
- **Tracks** outreach status (new → contacted → converted) and notes per lead
- **Learns** from your feedback — conversion rates adjust scoring weights over time
- **Reports** via a downloadable HTML report grouped by organisation type

---

## Setup

### Requirements

- Python 3.10+
- A terminal with internet access

### Install

```bash
# Clone
git clone https://github.com/Apsp31/care-home-lead-generator.git
cd care-home-lead-generator

# Create and activate venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and add your Companies House API key (optional — enables solicitors and estate agents):

```
COMPANIES_HOUSE_API_KEY=your_key_here
```

Get a free key at [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk).

All other data sources (NHS ODS, OpenStreetMap, postcodes.io, DuckDuckGo) are free and require no authentication.

### Run

```bash
source venv/bin/activate
streamlit run app.py
```

Opens at `http://localhost:8501`.

---

## Data sources

| Source | What it finds | Auth required |
|---|---|---|
| NHS ODS ORD API | GP surgeries, NHS trusts, Primary Care Networks | No |
| OpenStreetMap (Overpass) | Hospices, pharmacies, community centres, social services, places of worship | No |
| Companies House Public Data API | Solicitors, estate agents, IFAs, wealth managers — with named directors | API key (free) |
| CQC API | Domiciliary / homecare agencies with registered manager names | API key (free) |
| Web / Social (DuckDuckGo) | Dementia cafes, Age UK branches, carers groups, day centres, senior clubs — plus named contacts from LinkedIn | No |
| SOLLA | SOLLA-accredited care fees IFA specialists in the area | No |
| Google Places | Solicitors, IFAs, estate agents, pharmacies via Google Places | API key (paid, $200/mo free credit) |
| Retirement Villages | UK retirement villages — McCarthy & Stone, Churchill, Inspired Villages, Audley, Richmond, Rangeford, Birchgrove, ExtraCare, Pegasus Life, Housing 21, Anchor and more | No |

The web/social source searches LinkedIn for named professionals in the area (practice managers, discharge liaison nurses, private client solicitors, dementia coordinators etc.) and groups them by employer.

---

## Scoring

Each lead receives a priority score from 0–100:

| Component | Weight | Description |
|---|---|---|
| Org type | 40% | Likelihood of making referrals — GP (0.95), hospital discharge (0.95), solicitor (0.90), dementia cafe (0.85) … |
| Wealth indicator | 25% | Likelihood that clients can self-fund (~£2k/week) — private patient units (1.00), solicitors (0.95) … |
| Distance | 25% | Linear decay from 1.0 at 0 km to 0.0 at the search radius |
| Data completeness | 10% | Proportion of name/address/phone/email/website fields populated |

Scores are feedback-adjusted: after you log outcomes, organisation types with higher conversion rates score higher. The blend is `effective = static × 0.7 + conversion_rate × 0.3`. Apply updated weights via the **Re-score** button on the Lead Dashboard.

### Organisation type priorities

| Type | Priority | Wealth | Why |
|---|---|---|---|
| Hospital private patient unit | 0.95 | 1.00 | Already paying privately; coordinator manages placements |
| Hospital discharge / transfer of care | 0.95 | 0.75 | Primary route for post-hospital residential placements |
| GP surgery | 0.90 | 0.65 | Primary referral source; patient list includes elderly self-funders |
| Solicitor (private client) | 0.90 | 0.95 | Handles Wills, LPA, Probate for asset-rich elderly clients |
| Wealth manager | 0.90 | 0.95 | HNW asset management; care cost planning |
| Hospital dementia service | 0.90 | 0.80 | Families seeking specialist residential care |
| Dementia cafe / memory cafe | 0.85 | 0.85 | Families at the point of decision |
| Financial adviser (IFA/SOLLA) | 0.85 | 0.90 | Care fees planning specialists |
| Hospital frailty unit | 0.90 | 0.80 | Elderly patients transitioning to long-term care |
| Age UK branch | 0.80 | 0.60 | Trusted advisor; directly signposts to care |
| Social services | 0.80 | 0.60 | Identifies self-funders above £23,250 asset threshold |
| Hospice | 0.80 | 0.70 | Signposts families to residential care |
| Carers support group | 0.75 | 0.65 | Carers at crisis point — high referral conversion |
| PCN | 0.75 | 0.60 | Engaging the Clinical Director reaches multiple GP practices |
| Day centre | 0.65 | 0.45 | Trusted next-step advisor for families |
| Estate agent | 0.55 | 0.75 | Selling home to fund care; later-living specialists |
| Pharmacy | 0.55 | 0.35 | Trusted community touchpoint |
| Community group | 0.35 | 0.20 | Brand visibility and word-of-mouth |
| Nursing home (peer) | 0.30 | 0.25 | Cross-referrals when at capacity |
| Place of worship | 0.25 | 0.15 | Long-term relationship building |

---

## Pages

### New Search

Enter a care home name, postcode and radius. Select which data sources to query. Previous searches are saved and can be re-loaded to pre-fill the form.

The web/social source takes 3–5 minutes (DuckDuckGo rate limiting). NHS and OpenStreetMap typically complete in under 60 seconds.

### Lead Dashboard

View all leads for a search run. Toggle between:

- **By score** — all leads sorted by priority, hospitals grouped by parent with department sub-sections
- **By org type** — sections per organisation category (matching the HTML report), qualification note shown once per section

Filters: org type, status, minimum score. Export to a self-contained HTML report.

### Feedback / CRM

Update lead status (`new → contacted → converted / not converted / ignored`) and add notes. Status changes feed back into scoring weights.

### Scoring Weights

Shows current effective weight per organisation type alongside contacted/converted counts and conversion rate.

---

## Deployment (Streamlit Cloud + Neon)

### Persistent database with Neon

By default the app uses a local SQLite file (`leads.db`). On Streamlit Cloud the filesystem resets on every redeploy, so use [Neon](https://neon.tech) for a free persistent PostgreSQL database.

1. Sign up at **neon.tech** (free, no credit card required)
2. Create a new project — pick the region closest to your users
3. On the project dashboard go to **Connection Details**, select **Connection string**, and copy the `postgresql://...` URI
4. In Streamlit Cloud open your app → **Settings → Secrets** and add:

```toml
DATABASE_URL = "postgresql://user:password@host/dbname?sslmode=require"
```

5. Redeploy — the app creates all tables automatically on first boot

The app detects `DATABASE_URL` at startup. If it is set, PostgreSQL is used; if it is absent, SQLite is used (local dev). No code changes are needed when switching between the two.

Neon's free tier (512 MB, 1 project) never deletes data. Compute auto-suspends after 5 minutes of idle and wakes on the next request (~500 ms cold start).

---

## Project structure

```
app.py                       Streamlit UI — all pages
auth/
  auth.py                    User registration, login, session tokens
db/
  schema.py                  Connection factory (SQLite + PostgreSQL), schema init
  queries.py                 All SQL read/write helpers
sources/
  base.py                    DataSource abstract base class
  geocoder.py                postcodes.io geocoding + haversine distance
  nhs_ods.py                 NHS ODS ORD API — GPs, hospitals, PCNs
  overpass.py                OpenStreetMap Overpass — hospices, pharmacies, community
  companies_house.py         Companies House API — solicitors, IFAs, estate agents
  cqc.py                     CQC API — domiciliary care agencies
  web_search.py              DuckDuckGo — dementia cafes, LinkedIn contacts
  solla.py                   SOLLA care fees IFA specialists
  google_places.py           Google Places API — local businesses
  retirement_villages.py     UK retirement village operators (DDG-based)
  enrichment.py              Website contact scraper, LinkedIn enrichment
  hospital_enrichment.py     NHS Jobs + trust website enrichment for hospitals
scoring/
  rules.py                   Static base scores, wealth indicators, qualification notes
  engine.py                  Score calculation and feedback-blended weight adjustment
reports/
  html_report.py             Jinja2 HTML report generator
  templates/
    report.html              Report template
requirements.txt
.env.example
```

---

## Notes

- The database (`leads.db`) is created automatically on first run in the project directory; use Neon for persistent storage on Streamlit Cloud (see Deployment section above)
- The web/social source (DuckDuckGo) is rate-limited — space searches by at least 30 seconds if running multiple searches in quick succession
- Companies House search is national (no location filter on their API) — organisations are filtered by postcode geocoding after retrieval
- Hospital departments are expanded from a single hospital entry into up to 9 separate department leads (private patient unit, discharge, CHC, frailty, dementia, orthopaedics, stroke, social work, OT discharge)
- CQC source can be slow on large counties (~75 s for Hertfordshire at 5 km radius)

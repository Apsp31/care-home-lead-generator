"""Care Home Lead Generator — Streamlit app."""
import html as _html
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from urllib.parse import quote_plus

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from db.schema import init_db
from db import queries
from auth import auth as _auth
from sources.geocoder import postcode_to_latlon
from sources.nhs_ods import NHSODSSource
from sources.overpass import OverpassSource
from sources.companies_house import CompaniesHouseSource
from sources.web_search import WebSearchSource
from sources.solla import SollaSource
from sources.enrichment import enrich_contacts, ENRICH_ROLES
from scoring.engine import score_org, get_feedback_weights, recalculate_scores_for_run
from scoring.rules import QUALIFICATION_NOTES
from reports.html_report import generate_report, ORG_TYPE_LABELS, TYPE_ORDER

init_db()

st.set_page_config(
    page_title="Care Home Lead Generator",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auth state ─────────────────────────────────────────────────────────────────

if "user" not in st.session_state:
    st.session_state.user = None
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None

# Restore session from URL token on every load
if st.session_state.user is None:
    _t = st.query_params.get("t", "")
    if _t:
        _u = _auth.get_user_from_token(_t)
        if _u:
            st.session_state.user = _u
            st.session_state.auth_token = _t


def _show_landing():
    col_c, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        st.title("Care Home Lead Generator")
        st.markdown(
            "Referral outreach intelligence for care home admissions teams.  \n"
            "Find, score and track the professionals most likely to send you residents."
        )
        st.divider()
        tab_login, tab_register = st.tabs(["Log in", "Register"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Log in", type="primary",
                                                   use_container_width=True)
            if submitted:
                token, err = _auth.login_user(username, password)
                if err:
                    st.error(err)
                else:
                    user = _auth.get_user_from_token(token)
                    st.session_state.user = user
                    st.session_state.auth_token = token
                    st.query_params["t"] = token
                    st.rerun()

        with tab_register:
            st.caption("Anyone can register. No approval required.")
            with st.form("register_form"):
                new_username = st.text_input("Choose a username")
                new_password = st.text_input("Choose a password", type="password")
                submitted_reg = st.form_submit_button("Register", type="primary",
                                                       use_container_width=True)
            if submitted_reg:
                ok, err = _auth.register_user(new_username, new_password)
                if not ok:
                    st.error(err)
                else:
                    token, _ = _auth.login_user(new_username, new_password)
                    user = _auth.get_user_from_token(token)
                    st.session_state.user = user
                    st.session_state.auth_token = token
                    st.query_params["t"] = token
                    st.rerun()


# Gate: show landing if not logged in
if st.session_state.user is None:
    _show_landing()
    st.stop()

_current_user = st.session_state.user

# ── Sidebar navigation ────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Lead Generator")
    _ver = (Path(__file__).parent / "VERSION").read_text().strip()
    st.caption(f"Care home referral outreach · v{_ver}")
    st.divider()
    _admin_badge = " · admin" if _current_user["is_admin"] else ""
    st.markdown(f"Logged in as **{_current_user['username']}**{_admin_badge}")
    if st.button("Log out", use_container_width=True):
        _auth.logout_user(st.session_state.auth_token)
        st.session_state.user = None
        st.session_state.auth_token = None
        st.query_params.clear()
        st.rerun()
    st.divider()
    _nav_options = [
        "New Search", "Lead Dashboard", "Map View",
        "Feedback / CRM", "Scoring Weights", "Instructions",
    ]
    if _current_user["is_admin"]:
        _nav_options.append("Admin")
    page = st.radio(
        "Navigate",
        _nav_options,
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Set COMPANIES_HOUSE_API_KEY in .env to enable Companies House data.")
    st.caption("SOLLA source searches LinkedIn & web for care fees IFA specialists.")


# ── Helpers ───────────────────────────────────────────────────────────────────

STATUS_OPTIONS = ["new", "contacted", "converted", "not_converted", "ignored"]
STATUS_COLOURS = {
    "new": "blue",
    "contacted": "orange",
    "converted": "green",
    "not_converted": "red",
    "ignored": "gray",
}
STATUS_EMOJI = {
    "contacted":     "✅ ",
    "converted":     "🎉 ",
    "not_converted": "❌ ",
    "ignored":       "🚫 ",
    "new":           "",
}


def status_badge(status: str) -> str:
    colour = STATUS_COLOURS.get(status, "gray")
    return f":{colour}[{status.replace('_', ' ').upper()}]"


def _quick_status_buttons(lead_id: int, current_status: str, key_prefix: str = ""):
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        if current_status not in ("contacted", "converted"):
            if st.button("✓ Contacted", key=f"{key_prefix}contact_{lead_id}",
                         use_container_width=True, type="primary"):
                queries.update_lead_status(lead_id, "contacted")
                st.rerun()
        else:
            if st.button("↩ Unmark", key=f"{key_prefix}contact_{lead_id}",
                         use_container_width=True):
                queries.update_lead_status(lead_id, "new")
                st.rerun()
    with c2:
        if current_status != "ignored":
            if st.button("✗ Ignore", key=f"{key_prefix}ignore_{lead_id}",
                         use_container_width=True):
                queries.update_lead_status(lead_id, "ignored")
                st.rerun()
        else:
            if st.button("↩ Restore", key=f"{key_prefix}ignore_{lead_id}",
                         use_container_width=True):
                queries.update_lead_status(lead_id, "new")
                st.rerun()


def run_sources(lat, lon, radius_km, selected_sources, hospital_dept_types=None):
    sources = []
    if "NHS (GPs, hospitals, PCNs)" in selected_sources:
        sources.append(NHSODSSource())
    if "OpenStreetMap (hospices, pharmacies, community)" in selected_sources:
        ovp = OverpassSource()
        if hospital_dept_types is not None:
            ovp.dept_types = set(hospital_dept_types)
        sources.append(ovp)
    if "Companies House (solicitors, estate agents)" in selected_sources:
        sources.append(CompaniesHouseSource())
    if "Web / Social (dementia cafes, LinkedIn, Facebook)" in selected_sources:
        sources.append(WebSearchSource())
    if "SOLLA (care fees IFA specialists)" in selected_sources:
        sources.append(SollaSource())

    all_orgs = []
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(src.fetch, lat, lon, radius_km): src.name for src in sources}
        for fut in as_completed(futures):
            src_name = futures[fut]
            try:
                orgs = fut.result(timeout=60)
                all_orgs.extend(orgs)
            except Exception as e:
                errors.append(f"{src_name}: {e}")

    return all_orgs, errors


def save_orgs_to_db(orgs, run_id, radius_km, feedback_weights):
    for org in orgs:
        org_id = queries.upsert_organisation(org)
        if org_id is None:
            continue
        contacts = org.get("contacts", [])
        if contacts:
            queries.insert_contacts(org_id, contacts)
        score, breakdown = score_org(org, radius_km, feedback_weights)
        queries.upsert_lead(org_id, run_id, score, breakdown)


# ── Shared rendering helpers ──────────────────────────────────────────────────

def _social_links(name: str, town: str) -> str:
    q = quote_plus(f"{name} {town}".strip())
    li = f"https://www.linkedin.com/search/results/companies/?keywords={q}"
    fb = f"https://www.facebook.com/search/top?q={q}"
    return f"[LinkedIn]({li}) · [Facebook]({fb})"


def _safe_url(value: str) -> str:
    stripped = (value or "").strip().lower()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        return value.strip()
    return ""


def _render_contacts(contacts: list[dict]):
    """Named contacts in full; collapse pure-placeholder rows to a hint line."""
    real = [c for c in contacts
            if c.get("name") or c.get("email") or c.get("phone")]
    placeholders = [c for c in contacts
                    if not c.get("name") and not c.get("email") and not c.get("phone")]

    for c in real:
        if c.get("name"):
            st.markdown(f"- **{_html.escape(c['name'])}** — {_html.escape(c['role'])}")
        else:
            st.markdown(f"- _{_html.escape(c['role'])}_")
        if c.get("email"):
            st.markdown(f"  - Email: {_html.escape(c['email'])}")
        if c.get("phone"):
            st.markdown(f"  - Phone: {_html.escape(c['phone'])}")
        note = c.get("source_notes", "")
        if note and note not in ("Role placeholder",):
            st.caption(f"  {_html.escape(note)}")

    if placeholders:
        roles = " · ".join(_html.escape(c["role"]) for c in placeholders)
        prefix = "Also look for:" if real else "If no named contact found, look for:"
        st.caption(f"{prefix} {roles}")

    if not real and not placeholders:
        st.caption("No contacts on record — use Find online links to locate.")


def _render_lead_card(lead: dict, show_qual_note: bool = True, expanded: bool = False):
    score = lead["priority_score"]
    sc = "green" if score >= 0.7 else ("orange" if score >= 0.4 else "red")
    contacts = queries.get_contacts_for_org(lead["org_id"])

    emoji = STATUS_EMOJI.get(lead["status"], "")
    with st.expander(
        f"{emoji}**{_html.escape(lead['name'])}** | :{sc}[{int(score*100)}] | "
        f"{_html.escape(_org_label(lead))} | "
        f"{lead['distance_km'] or '?'} km | {status_badge(lead['status'])}",
        expanded=expanded,
    ):
        if show_qual_note:
            qual = QUALIFICATION_NOTES.get(lead["org_type"], "")
            if qual:
                st.info(qual)

        c1, c2 = st.columns(2)
        with c1:
            parts = [lead.get("address_line1"), lead.get("address_line2"),
                     lead.get("town"), lead.get("postcode")]
            addr = _html.escape(", ".join(p for p in parts if p))
            st.markdown(f"**Address:** {addr or '—'}")
            if lead.get("phone"):
                st.markdown(f"**Phone:** {_html.escape(lead['phone'])}")
            if lead.get("email"):
                st.markdown(f"**Email:** {_html.escape(lead['email'])}")
            site = _safe_url(lead.get("website", ""))
            if site:
                st.markdown(f"**Website:** [{site}]({site})")
            st.markdown(f"**Distance:** {lead.get('distance_km')} km")
            st.markdown(f"**Find online:** {_social_links(lead['name'], lead.get('town', ''))}")
            breakdown = json.loads(lead.get("score_breakdown") or "{}")
            if breakdown:
                bd = breakdown
                st.caption(
                    f"Score: type={bd.get('type_score','?')} · "
                    f"wealth={bd.get('wealth_indicator','?')} · "
                    f"dist={bd.get('distance_score','?')} · "
                    f"completeness={bd.get('completeness','?')}"
                )
        with c2:
            st.markdown("**Contacts:**")
            _render_contacts(contacts)

        if lead.get("notes"):
            st.markdown(f"**Notes:** {_html.escape(lead['notes'])}")

        st.divider()
        _quick_status_buttons(lead["id"], lead["status"], key_prefix=f"lc_{lead['id']}_")


def _render_hospital_group(parent_name: str, depts: list, show_dept_qual: bool = True):
    best = max(depts, key=lambda x: x["priority_score"])
    sc = "green" if best["priority_score"] >= 0.7 else (
        "orange" if best["priority_score"] >= 0.4 else "red")
    with st.expander(
        f"🏥 **{_html.escape(parent_name)}** | :{sc}[{int(best['priority_score']*100)}] | "
        f"{len(depts)} departments | {best['distance_km'] or '?'} km",
        expanded=False,
    ):
        d = depts[0]
        parts = [d.get("address_line1"), d.get("address_line2"),
                 d.get("town"), d.get("postcode")]
        addr = ", ".join(p for p in parts if p)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Address:** {_html.escape(addr) or '—'}")
            if d.get("phone"):
                st.markdown(f"**Switchboard:** {_html.escape(d['phone'])}")
            if d.get("email"):
                st.markdown(f"**Email:** {_html.escape(d['email'])}")
            site = _safe_url(d.get("website", ""))
            if site:
                st.markdown(f"**Website:** [{site}]({site})")
            st.markdown(f"**Distance:** {d.get('distance_km')} km")
            st.markdown(f"**Find online:** {_social_links(parent_name, d.get('town', ''))}")
        with c2:
            st.markdown(f"**{len(depts)} departments to target** — expand each below.")

        st.divider()
        for dept in sorted(depts, key=lambda x: x["priority_score"], reverse=True):
            dept_label = dept["name"].split(" — ", 1)[1] if " — " in dept["name"] else dept["name"]
            dept_contacts = queries.get_contacts_for_org(dept["org_id"])
            score = dept["priority_score"]
            sc2 = "green" if score >= 0.7 else ("orange" if score >= 0.4 else "red")
            demoji = STATUS_EMOJI.get(dept["status"], "")
            with st.expander(
                f"{demoji}**{dept_label}** | :{sc2}[{int(score*100)}] | {status_badge(dept['status'])}",
                expanded=False,
            ):
                if show_dept_qual:
                    qual = QUALIFICATION_NOTES.get(dept["org_type"], "")
                    if qual:
                        st.info(qual)
                _render_contacts(dept_contacts)
                if dept.get("notes"):
                    st.markdown(f"**Notes:** {_html.escape(dept['notes'])}")
                st.divider()
                _quick_status_buttons(dept["id"], dept["status"], key_prefix=f"hd_{dept['id']}_")


def _org_label(lead: dict) -> str:
    if lead.get("source") == "solla":
        return "SOLLA Care Fees IFA"
    return ORG_TYPE_LABELS.get(lead["org_type"], lead["org_type"].replace("_", " ").title())


def _group_score(lst: list) -> float:
    return max(l["priority_score"] for l in lst)


# ── Page: New Search ──────────────────────────────────────────────────────────

ALL_SOURCES = [
    "NHS (GPs, hospitals, PCNs)",
    "OpenStreetMap (hospices, pharmacies, community)",
    "Companies House (solicitors, estate agents)",
    "Web / Social (dementia cafes, LinkedIn, Facebook)",
    "SOLLA (care fees IFA specialists)",
]
DEFAULT_SOURCES = [
    "NHS (GPs, hospitals, PCNs)",
    "OpenStreetMap (hospices, pharmacies, community)",
    "Web / Social (dementia cafes, LinkedIn, Facebook)",
]

# Org categories for the picker — label → list of org_type strings
ORG_CATEGORY_OPTIONS: dict[str, list[str]] = {
    "Hospital departments": [
        "hospital_private", "hospital_discharge", "hospital_chc",
        "hospital_frailty", "hospital_dementia", "hospital_ortho",
        "hospital_stroke", "hospital_social_work", "hospital_ot_discharge",
    ],
    "GP surgeries":           ["GP"],
    "PCNs":                   ["PCN"],
    "Hospices":               ["hospice"],
    "Pharmacies":             ["pharmacy"],
    "Solicitors":             ["solicitor"],
    "Wealth managers":        ["wealth_manager"],
    "IFAs":                   ["financial_adviser"],
    "Estate agents":          ["estate_agent"],
    "Social services":        ["social_services"],
    "Dementia / memory cafes":["dementia_cafe"],
    "Age UK branches":        ["age_uk_branch"],
    "Carers groups":          ["carers_group"],
    "Day centres":            ["day_centre"],
    "Community groups":       ["community_group"],
    "Places of worship":      ["place_of_worship"],
    "Care homes (peer)":      ["nursing_home"],
}
ALL_ORG_CATEGORIES = list(ORG_CATEGORY_OPTIONS.keys())

# Hospital department sub-picker — label → org_type string
HOSPITAL_DEPT_OPTIONS: dict[str, str] = {
    "Private Patient Unit":         "hospital_private",
    "Discharge / Transfer of Care": "hospital_discharge",
    "Continuing Healthcare (CHC)":  "hospital_chc",
    "Frailty & Elderly Care":       "hospital_frailty",
    "Memory Clinic / Dementia":     "hospital_dementia",
    "Trauma & Orthopaedics":        "hospital_ortho",
    "Stroke Rehabilitation":        "hospital_stroke",
    "Social Work Department":       "hospital_social_work",
    "Discharge Occupational Therapy":"hospital_ot_discharge",
}
ALL_HOSPITAL_DEPTS = list(HOSPITAL_DEPT_OPTIONS.keys())

if page == "New Search":
    st.header("New Lead Search")

    # Previous search selector — pre-fills the form below (scoped to this user)
    _uid_for_prev = None if _current_user["is_admin"] else _current_user["id"]
    prev_runs = queries.get_distinct_care_homes(user_id=_uid_for_prev)
    prefill: dict = {}
    if prev_runs:
        options = ["— New search —"] + [
            f"{p['care_home_name']}  ({p['postcode']})" for p in prev_runs
        ]
        choice = st.selectbox("Load previous search", options)
        if choice != "— New search —":
            idx = options.index(choice) - 1
            prefill = prev_runs[idx]

    prefill_sources = json.loads(prefill.get("sources") or "[]") or DEFAULT_SOURCES
    prefill_org_cats = json.loads(prefill.get("org_types") or "null") or ALL_ORG_CATEGORIES
    prefill_hosp_depts = json.loads(prefill.get("hospital_depts") or "null") or ALL_HOSPITAL_DEPTS

    with st.form("search_form"):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            care_home = st.text_input("Care Home Name",
                value=prefill.get("care_home_name", ""),
                placeholder="Sunrise Care Home")
        with col2:
            postcode = st.text_input("Postcode",
                value=prefill.get("postcode", ""),
                placeholder="SW1A 1AA")
        with col3:
            radius = st.number_input("Radius (km)", min_value=1.0, max_value=30.0,
                                     value=float(prefill.get("radius_km", 5.0)), step=0.5)

        st.markdown("**Data Sources**")
        selected_sources = st.multiselect(
            "Select sources to query",
            options=ALL_SOURCES,
            default=prefill_sources,
        )

        with st.expander("Organisation filters & enrichment"):
            st.markdown("**Organisation categories to include**")
            selected_org_cats = st.multiselect(
                "Categories",
                options=ALL_ORG_CATEGORIES,
                default=prefill_org_cats,
                label_visibility="collapsed",
            )

            st.markdown("**Hospital departments to generate** *(applies when OpenStreetMap source is selected)*")
            selected_hosp_dept_labels = st.multiselect(
                "Hospital departments",
                options=ALL_HOSPITAL_DEPTS,
                default=prefill_hosp_depts,
                label_visibility="collapsed",
            )

            st.markdown("**Contact enrichment**")
            enrich_enabled = st.checkbox(
                "Search LinkedIn for named contacts on orgs without them  *(adds ~2–3 min)*",
                value=False,
            )

        submitted = st.form_submit_button("Run Search", type="primary", use_container_width=True)

    if submitted:
        if not care_home or not postcode:
            st.error("Please enter a care home name and postcode.")
        elif not selected_sources:
            st.error("Select at least one data source.")
        else:
            with st.spinner("Geocoding postcode..."):
                try:
                    lat, lon = postcode_to_latlon(postcode)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()

            st.success(f"Location: {lat:.4f}, {lon:.4f}")

            # Resolve selected org types from category labels
            selected_org_types: list[str] = []
            for cat in selected_org_cats:
                selected_org_types.extend(ORG_CATEGORY_OPTIONS.get(cat, []))
            selected_org_types_set = set(selected_org_types)

            # Resolve selected hospital department org_type strings
            selected_hosp_dept_types = [
                HOSPITAL_DEPT_OPTIONS[lbl]
                for lbl in selected_hosp_dept_labels
                if lbl in HOSPITAL_DEPT_OPTIONS
            ]

            feedback_weights = get_feedback_weights()
            run_id = queries.create_search_run(
                care_home, postcode, radius, lat, lon,
                selected_sources,
                org_types=selected_org_cats if selected_org_cats != ALL_ORG_CATEGORIES else None,
                hospital_depts=selected_hosp_dept_labels
                    if selected_hosp_dept_labels != ALL_HOSPITAL_DEPTS else None,
                user_id=_current_user["id"],
            )

            progress = st.progress(0, text="Querying data sources...")
            with st.spinner("Fetching leads from data sources (this may take 30–60 seconds)..."):
                orgs, errors = run_sources(
                    lat, lon, radius, selected_sources,
                    hospital_dept_types=selected_hosp_dept_types
                        if selected_hosp_dept_labels != ALL_HOSPITAL_DEPTS else None,
                )

            # Filter by selected org categories (if not all selected)
            if selected_org_types_set and selected_org_types_set != set(
                t for types in ORG_CATEGORY_OPTIONS.values() for t in types
            ):
                orgs = [o for o in orgs if o.get("org_type") in selected_org_types_set]

            if enrich_enabled and orgs:
                enrich_types = set(selected_org_types_set) & set(ENRICH_ROLES)
                progress.progress(50, text=f"Enriching contacts via LinkedIn ({len(enrich_types)} org types)...")
                orgs = enrich_contacts(orgs, enrich_types if enrich_types else None)

            progress.progress(70, text="Scoring and saving leads...")
            save_orgs_to_db(orgs, run_id, radius, feedback_weights)
            progress.progress(100, text="Done.")

            if errors:
                for err in errors:
                    st.warning(f"Source error: {err}")

            st.success(f"Found **{len(orgs)}** organisations. Run ID: {run_id}")
            st.info("Go to **Lead Dashboard** to view and export results.")
            st.session_state["active_run_id"] = run_id


# ── Page: Lead Dashboard ──────────────────────────────────────────────────────

elif page == "Lead Dashboard":
    st.header("Lead Dashboard")

    _uid = None if _current_user["is_admin"] else _current_user["id"]
    runs = queries.get_all_search_runs(user_id=_uid)
    if not runs:
        st.info("No searches yet. Run a search first.")
        st.stop()

    run_options = {f"{r['care_home_name']} — {r['postcode']} ({r['run_at'][:10]}) [#{r['id']}]": r["id"]
                  for r in runs}
    default_run = st.session_state.get("active_run_id", runs[0]["id"])
    default_label = next((k for k, v in run_options.items() if v == default_run), list(run_options.keys())[0])

    selected_label = st.selectbox("Search Run", list(run_options.keys()),
                                   index=list(run_options.keys()).index(default_label))
    run_id = run_options[selected_label]
    run = queries.get_search_run(run_id)

    leads = queries.get_leads_for_run(run_id)
    if not leads:
        st.info("No leads found for this run.")
        st.stop()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        type_filter = st.multiselect(
            "Org type",
            options=sorted(set(l["org_type"] for l in leads)),
            default=[],
        )
    with col2:
        status_filter = st.multiselect(
            "Status",
            options=STATUS_OPTIONS,
            default=["new", "contacted", "converted", "not_converted"],
        )
    with col3:
        min_score = st.slider("Min priority score", 0.0, 1.0, 0.0, 0.05)

    filtered = leads
    if type_filter:
        filtered = [l for l in filtered if l["org_type"] in type_filter]
    if status_filter:
        filtered = [l for l in filtered if l["status"] in status_filter]
    filtered = [l for l in filtered if l["priority_score"] >= min_score]

    st.caption(f"Showing {len(filtered)} of {len(leads)} leads")

    # Report export
    col_a, col_b, _ = st.columns([1, 1, 3])
    with col_a:
        if st.button("Generate HTML Report", type="primary"):
            html = generate_report(run_id)
            st.download_button(
                "Download report",
                data=html.encode(),
                file_name=f"leads_{run_id}.html",
                mime="text/html",
            )
    with col_b:
        if st.button("Re-score (apply feedback)"):
            recalculate_scores_for_run(run_id, run["radius_km"])
            st.success("Scores updated from feedback data.")
            st.rerun()

    # ── View mode toggle ───────────────────────────────────────────────────────
    view_mode = st.radio(
        "Group by",
        ["By org type", "By score"],
        horizontal=True,
        label_visibility="collapsed",
    )

    HOSP_PREFIX = "hospital_"
    hosp_leads = [l for l in filtered if l["org_type"].startswith(HOSP_PREFIX)]
    other_leads = [l for l in filtered if not l["org_type"].startswith(HOSP_PREFIX)]

    hosp_groups: dict[str, list] = {}
    for lead in hosp_leads:
        parent = lead["name"].split(" — ")[0] if " — " in lead["name"] else lead["name"]
        hosp_groups.setdefault(parent, []).append(lead)

    if view_mode == "By score":
        render_items = (
            [(l["priority_score"], "lead", l) for l in other_leads]
            + [(_group_score(depts), "hospital", (parent, depts))
               for parent, depts in hosp_groups.items()]
        )
        render_items.sort(key=lambda x: x[0], reverse=True)
        for _, kind, payload in render_items:
            if kind == "lead":
                _render_lead_card(payload, show_qual_note=True)
            else:
                _render_hospital_group(payload[0], payload[1], show_dept_qual=True)

    else:  # By org type
        # Build type → leads mapping; split SOLLA from regular IFAs
        type_map: dict[str, list] = {}
        for lead in other_leads:
            if lead["org_type"] == "financial_adviser" and lead.get("source") == "solla":
                type_map.setdefault("_solla", []).append(lead)
            else:
                type_map.setdefault(lead["org_type"], []).append(lead)

        # Hospital types → one virtual bucket per parent hospital group
        if hosp_groups:
            type_map["_hospitals"] = list(hosp_groups.items())

        # Render in priority order; inject _solla after financial_adviser slot
        ordered_types: list[str] = []
        for t in TYPE_ORDER:
            if t == "financial_adviser":
                if "_solla" in type_map:
                    ordered_types.append("_solla")
                if t in type_map:
                    ordered_types.append(t)
            elif t in type_map or t == "_hospitals":
                ordered_types.append(t)
        for t in type_map:
            if t not in ordered_types:
                ordered_types.append(t)

        for org_type in ordered_types:
            if org_type not in type_map:
                continue
            items = type_map[org_type]

            if org_type == "_hospitals":
                label = "Hospitals (all departments)"
                qual = QUALIFICATION_NOTES.get("hospital_discharge", "")
                n = sum(len(d) for _, d in items)
            elif org_type == "_solla":
                label = "SOLLA Care Fees IFAs"
                qual = QUALIFICATION_NOTES.get("financial_adviser", "")
                n = len(items)
            else:
                label = ORG_TYPE_LABELS.get(org_type, org_type.replace("_", " ").title())
                qual = QUALIFICATION_NOTES.get(org_type, "")
                n = len(items)

            new_count = sum(
                1 for x in (items if org_type != "_hospitals"
                            else [l for _, d in items for l in d])
                if x.get("status") == "new"
            )
            badge = f" · {new_count} new" if new_count else ""
            with st.expander(f"**{label}** ({n}{badge})", expanded=False):
                if qual:
                    st.info(qual)
                if org_type == "_hospitals":
                    for parent_name, depts in sorted(items, key=lambda x: _group_score(x[1]), reverse=True):
                        _render_hospital_group(parent_name, depts, show_dept_qual=False)
                else:
                    for lead in sorted(items, key=lambda x: x["priority_score"], reverse=True):
                        _render_lead_card(lead, show_qual_note=False)


# ── Page: Map View ────────────────────────────────────────────────────────────

elif page == "Map View":
    import folium
    from streamlit_folium import st_folium

    st.header("Map View")

    _uid = None if _current_user["is_admin"] else _current_user["id"]
    runs = queries.get_all_search_runs(user_id=_uid)
    if not runs:
        st.info("No searches yet. Run a search first.")
        st.stop()

    run_options = {
        f"{r['care_home_name']} — {r['postcode']} ({r['run_at'][:10]}) [#{r['id']}]": r["id"]
        for r in runs
    }
    default_run = st.session_state.get("active_run_id", runs[0]["id"])
    default_label = next(
        (k for k, v in run_options.items() if v == default_run),
        list(run_options.keys())[0]
    )
    selected_label = st.selectbox(
        "Search Run", list(run_options.keys()),
        index=list(run_options.keys()).index(default_label),
        key="map_run_select",
    )
    run_id = run_options[selected_label]
    run = queries.get_search_run(run_id)

    leads = queries.get_leads_for_run(run_id)
    if not leads:
        st.info("No leads for this run.")
        st.stop()

    # ── Filters ───────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        map_type_filter = st.multiselect(
            "Org categories",
            options=ALL_ORG_CATEGORIES,
            default=[],
            key="map_type_filter",
            placeholder="All categories",
        )
    with col2:
        map_status_filter = st.multiselect(
            "Status",
            options=STATUS_OPTIONS,
            default=["new", "contacted", "converted", "not_converted"],
            key="map_status_filter",
        )
    with col3:
        map_min_score = st.slider("Min score", 0.0, 1.0, 0.0, 0.05, key="map_score")

    if map_type_filter:
        allowed_types: set[str] | None = set()
        for cat in map_type_filter:
            allowed_types.update(ORG_CATEGORY_OPTIONS.get(cat, []))
    else:
        allowed_types = None

    filtered_leads = [
        l for l in leads
        if l["priority_score"] >= map_min_score
        and (not allowed_types or l["org_type"] in allowed_types)
        and (not map_status_filter or l["status"] in map_status_filter)
        and l.get("lat") and l.get("lon")
    ]

    precise  = [l for l in filtered_leads if l.get("distance_km", 0) > 0.01]
    estimated = [l for l in filtered_leads if l.get("distance_km", 0) <= 0.01]

    care_lat  = run["lat"]
    care_lon  = run["lon"]
    radius_km = run["radius_km"]

    # Pre-fetch contacts for all visible leads (single batch query)
    all_visible_org_ids = [l["org_id"] for l in filtered_leads]
    contacts_map = queries.get_contacts_for_orgs(all_visible_org_ids)

    st.caption(
        f"Showing **{len(precise)}** leads with known locations"
        + (f" + {len(estimated)} estimated" if estimated else "")
        + f" · {len(leads) - len(filtered_leads)} hidden by filters"
        + " · click a marker for full details"
    )

    # ── High-contrast colour palette ──────────────────────────────────────────
    _ORG_COLOUR: dict[str, str] = {
        # Hospitals — red family (darkest shades for highest priority)
        "hospital_private":      "#7f0000",
        "hospital_discharge":    "#b71c1c",
        "hospital_chc":          "#880e4f",   # deep magenta — CHC sits outside the ward family
        "hospital_frailty":      "#c62828",
        "hospital_dementia":     "#d32f2f",
        "hospital_ortho":        "#e53935",
        "hospital_stroke":       "#ef5350",
        "hospital_social_work":  "#f44336",
        "hospital_ot_discharge": "#ad1457",   # mid magenta — OT discharge adjacent to CHC
        # Primary care — strong blue
        "GP":                   "#0d47a1",
        "PCN":                  "#1565c0",
        # Clinical — dark teal
        "hospice":              "#004d40",
        "pharmacy":             "#00695c",
        # Professional referrers — deep purple
        "solicitor":            "#4a148c",
        "wealth_manager":       "#6a1b9a",
        "financial_adviser":    "#7b1fa2",
        "estate_agent":         "#8e24aa",
        # Statutory — navy
        "social_services":      "#1a237e",
        # Community specialist — dark green
        "dementia_cafe":        "#1b5e20",
        "age_uk_branch":        "#2e7d32",
        "carers_group":         "#33691e",
        "day_centre":           "#558b2f",
        # Community general — burnt orange (stands out from greens)
        "community_group":      "#bf360c",
        "place_of_worship":     "#e64a19",
        # Care homes
        "nursing_home":         "#4e342e",
    }

    def _marker_style(lead: dict) -> dict:
        status = lead.get("status", "new")
        base = _ORG_COLOUR.get(lead["org_type"], "#37474f")
        if status == "ignored":
            return dict(fill_color="#9e9e9e", color="#bdbdbd", weight=1,
                        fill_opacity=0.35, radius=7, dash_array="4 3")
        if status in ("contacted", "converted"):
            fill = "#2e7d32" if status == "converted" else base
            return dict(fill_color=fill, color="#ffffff", weight=3,
                        fill_opacity=0.95, radius=11)
        return dict(fill_color=base, color="#ffffff", weight=1.5,
                    fill_opacity=0.9, radius=9)

    def _popup_html(lead: dict, contacts: list[dict], estimated: bool = False) -> str:
        score_pct = int(lead["priority_score"] * 100)
        label     = _org_label(lead)
        score_bg  = "#d4edda" if score_pct >= 70 else ("#fff3cd" if score_pct >= 40 else "#f8d7da")
        parts     = [lead.get("address_line1"), lead.get("town"), lead.get("postcode")]
        addr      = _html.escape(", ".join(p for p in parts if p) or "—")
        phone_str = f"<br/>📞 {_html.escape(lead['phone'])}" if lead.get("phone") else ""
        dist_str  = f"<br/>📍 {lead['distance_km']} km" if lead.get("distance_km") else ""
        est_str   = "<br/><i style='color:#888;font-size:10px'>Location estimated</i>" if estimated else ""
        name_e    = _html.escape(lead["name"])
        _status_colours = {"contacted": ("#e8f5e9","#2e7d32"), "converted": ("#c8e6c9","#1b5e20"),
                           "ignored": ("#f5f5f5","#9e9e9e"), "not_converted": ("#ffebee","#c62828"),
                           "new": ("#e3f2fd","#1565c0")}
        _sbg, _sfg = _status_colours.get(lead["status"], ("#f5f5f5","#555"))
        status_e  = (f"<span style='background:{_sbg};color:{_sfg};padding:1px 5px;"
                     f"border-radius:3px;font-size:10px;font-weight:700'>"
                     f"{_html.escape(lead['status'].replace('_',' ').upper())}</span>")

        # Contacts section
        real_contacts = [c for c in contacts if c.get("name") or c.get("email") or c.get("phone")]
        placeholder_roles = [c["role"] for c in contacts
                             if not c.get("name") and not c.get("email") and c.get("role")]
        contact_html = ""
        if real_contacts:
            rows = "".join(
                f"<b>{_html.escape(c['name'])}</b> — {_html.escape(c['role'])}<br/>"
                if c.get("name") else f"{_html.escape(c['role'])}<br/>"
                for c in real_contacts[:3]
            )
            contact_html = f"<hr style='margin:5px 0'/><b>Contacts:</b><br/>{rows}"
        elif placeholder_roles:
            roles_e = " · ".join(_html.escape(r) for r in placeholder_roles[:3])
            contact_html = (
                f"<hr style='margin:5px 0'/>"
                f"<span style='color:#777;font-size:10px'>Look for: {roles_e}</span>"
            )

        return (
            f"<div style='font-family:sans-serif;min-width:190px;max-width:270px'>"
            f"<b style='font-size:13px'>{name_e}</b><br/>"
            f"<span style='color:#555;font-size:11px'>{label}</span><br/>"
            f"<span style='background:{score_bg};padding:2px 6px;border-radius:3px;"
            f"font-size:11px;font-weight:700'>Score {score_pct}</span> "
            f"{status_e}"
            f"<br/><span style='font-size:11px;color:#555'>{addr}{phone_str}{dist_str}</span>"
            f"{contact_html}"
            f"{est_str}"
            f"<hr style='margin:5px 0'/>"
            f"<span style='font-size:11px;color:#1a6ec7'>▼ Full details shown below map</span>"
            f"</div>"
        )

    # ── Build folium map ──────────────────────────────────────────────────────
    m = folium.Map(
        location=[care_lat, care_lon],
        zoom_start=13,
        tiles="CartoDB positron",
    )

    # Radius rings
    folium.Circle(
        location=[care_lat, care_lon], radius=radius_km * 1000,
        color="#1a3a5c", fill=True, fill_opacity=0.04, weight=2,
        tooltip=f"{radius_km} km radius",
    ).add_to(m)
    if radius_km > 2:
        folium.Circle(
            location=[care_lat, care_lon], radius=radius_km * 500,
            color="#1a3a5c", fill=False, weight=1.2, dash_array="6 5",
            tooltip=f"{radius_km/2:.1g} km",
        ).add_to(m)
    if radius_km > 4:
        folium.Circle(
            location=[care_lat, care_lon], radius=radius_km * 1000 / 3,
            color="#1a3a5c", fill=False, weight=1, dash_array="3 8",
            tooltip=f"{radius_km/3:.1g} km",
        ).add_to(m)

    # Care home marker
    folium.Marker(
        location=[care_lat, care_lon],
        popup=folium.Popup(
            f"<b>{run['care_home_name']}</b><br/>{run['postcode']}", max_width=200
        ),
        tooltip=run["care_home_name"],
        icon=folium.Icon(color="red", icon="home", prefix="fa"),
    ).add_to(m)

    # Lead markers — precise location
    for lead in precise:
        style    = _marker_style(lead)
        contacts = contacts_map.get(lead["org_id"], [])
        folium.CircleMarker(
            location=[lead["lat"], lead["lon"]],
            fill=True,
            popup=folium.Popup(_popup_html(lead, contacts, estimated=False), max_width=280),
            tooltip=lead["name"],
            **style,
        ).add_to(m)

    # Estimated-location leads — clustered near care home, dashed border
    for i, lead in enumerate(estimated):
        offset   = i * 0.0003
        style    = _marker_style(lead)
        contacts = contacts_map.get(lead["org_id"], [])
        # Merge estimated dash styling (always dashed) with status style
        est_style = {**style, "radius": max(style["radius"] - 2, 5),
                     "fill_opacity": style["fill_opacity"] * 0.65,
                     "dash_array": "4 3"}
        folium.CircleMarker(
            location=[care_lat + offset, care_lon + offset * 0.7],
            fill=True,
            popup=folium.Popup(_popup_html(lead, contacts, estimated=True), max_width=280),
            tooltip=f"{lead['name']} (est.)",
            **est_style,
        ).add_to(m)

    # Fit bounds
    all_lats = [care_lat] + [l["lat"] for l in precise]
    all_lons = [care_lon] + [l["lon"] for l in precise]
    if len(all_lats) > 1:
        m.fit_bounds([
            [min(all_lats) - 0.002, min(all_lons) - 0.002],
            [max(all_lats) + 0.002, max(all_lons) + 0.002],
        ])

    # ── Legend ────────────────────────────────────────────────────────────────
    type_legend_rows = [
        ("Hospitals",              "#b71c1c"),
        ("GP / PCN",               "#0d47a1"),
        ("Clinical",               "#004d40"),
        ("Professional referrers", "#4a148c"),
        ("Statutory",              "#1a237e"),
        ("Community (specialist)", "#1b5e20"),
        ("Community (general)",    "#bf360c"),
        ("Care homes (peer)",      "#4e342e"),
        ("Estimated location",     "#546e7a"),
    ]
    status_legend_rows = [
        ("New",         "#555", "#ffffff", "1.5px", "9px", ""),
        ("Contacted",   "#555", "#ffffff", "3px",   "11px", ""),
        ("Converted",   "#2e7d32", "#ffffff", "3px", "11px", ""),
        ("Ignored",     "#9e9e9e", "#bdbdbd", "1px", "7px",  "4 3"),
    ]

    def _status_dot(fill: str, border: str, bw: str, sz: str, dash: str) -> str:
        outline = f"border:{bw} dashed {border}" if dash else f"border:{bw} solid {border}"
        return (f"<span style='display:inline-block;width:{sz};height:{sz};"
                f"background:{fill};border-radius:50%;{outline};"
                f"margin-right:6px;vertical-align:middle'></span>")

    legend_html = (
        "<div style='position:fixed;bottom:28px;left:28px;z-index:1000;"
        "background:rgba(255,255,255,0.95);padding:10px 14px;border-radius:7px;"
        "box-shadow:0 2px 6px rgba(0,0,0,.25);font-family:sans-serif;font-size:11px;"
        "line-height:1.8'>"
        "<b style='font-size:12px'>Lead types</b><br/>"
        + "".join(
            f"<span style='display:inline-block;width:11px;height:11px;"
            f"background:{c};border-radius:50%;margin-right:6px;vertical-align:middle'>"
            f"</span>{g}<br/>"
            for g, c in type_legend_rows
        )
        + "<span style='font-size:16px;vertical-align:middle;margin-right:4px'>📍</span>"
          "<b>Care home</b><br/>"
        + "<hr style='margin:5px 0;border:none;border-top:1px solid #ddd'/>"
          "<b style='font-size:12px'>Status</b><br/>"
        + "".join(
            f"{_status_dot(fill, border, bw, sz, dash)}{label}<br/>"
            for label, fill, border, bw, sz, dash in status_legend_rows
        )
        + "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # ── Render map and detect click ───────────────────────────────────────────
    map_result = st_folium(
        m, use_container_width=True, height=620,
        returned_objects=["last_object_clicked"],
    )

    # ── Full lead detail panel below map ──────────────────────────────────────
    clicked = (map_result or {}).get("last_object_clicked")
    if clicked:
        clat = clicked.get("lat")
        clng = clicked.get("lng")
        if clat is not None and clng is not None:
            # Match click to nearest lead (precise or estimated)
            candidates = precise + [
                {**l, "lat": care_lat + i * 0.0003, "lon": care_lon + i * 0.0003 * 0.7}
                for i, l in enumerate(estimated)
            ]
            if candidates:
                closest = min(
                    candidates,
                    key=lambda l: (l["lat"] - clat) ** 2 + (l["lon"] - clng) ** 2,
                )
                dist_sq = (closest["lat"] - clat) ** 2 + (closest["lon"] - clng) ** 2
                # Only show if click is close enough to a marker (within ~200 m)
                if dist_sq < 0.0005:
                    st.divider()
                    st.subheader("Selected Lead")
                    _render_lead_card(closest, show_qual_note=True, expanded=True)


# ── Page: Feedback / CRM ──────────────────────────────────────────────────────

elif page == "Feedback / CRM":
    st.header("Feedback & CRM")
    st.caption("Update lead status and notes. Feedback is used to re-weight prioritisation.")

    _uid = None if _current_user["is_admin"] else _current_user["id"]
    runs = queries.get_all_search_runs(user_id=_uid)
    if not runs:
        st.info("No searches yet.")
        st.stop()

    run_options = {f"{r['care_home_name']} — {r['postcode']} ({r['run_at'][:10]}) [#{r['id']}]": r["id"]
                  for r in runs}
    selected_label = st.selectbox("Search Run", list(run_options.keys()))
    run_id = run_options[selected_label]

    leads = queries.get_leads_for_run(run_id)
    if not leads:
        st.info("No leads for this run.")
        st.stop()

    # Filter to non-ignored leads by default
    show_ignored = st.checkbox("Show ignored leads", value=False)
    if not show_ignored:
        leads = [l for l in leads if l["status"] != "ignored"]

    st.divider()

    def _crm_lead_row(lead: dict):
        contacts = queries.get_contacts_for_org(lead["org_id"])
        emoji = STATUS_EMOJI.get(lead["status"], "")
        with st.expander(
            f"{emoji}**{_html.escape(lead['name'])}** | {status_badge(lead['status'])}",
            expanded=(lead["status"] == "new"),
        ):
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown("**Key contacts:**")
                for c in contacts:
                    if c.get("name"):
                        st.markdown(f"- **{_html.escape(c['name'])}** — {_html.escape(c['role'])}")
                    else:
                        st.markdown(f"- _{_html.escape(c['role'])}_")
                st.markdown(f"**Priority score:** {int(lead['priority_score']*100)}")
                qual = QUALIFICATION_NOTES.get(lead["org_type"], "")
                if qual:
                    st.caption(qual)
            with col2:
                new_status = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(lead["status"]),
                    key=f"status_{lead['id']}",
                )
                new_notes = st.text_area(
                    "Notes",
                    value=lead.get("notes") or "",
                    key=f"notes_{lead['id']}",
                    height=80,
                )
                if st.button("Save", key=f"save_{lead['id']}"):
                    queries.update_lead_status(lead["id"], new_status, new_notes)
                    st.success("Saved.")
                    st.rerun()

    # ── Build category groups ──────────────────────────────────────────────────
    HOSP_SUB_ORDER = [
        "hospital_private", "hospital_discharge", "hospital_chc",
        "hospital_frailty", "hospital_dementia", "hospital_ortho",
        "hospital_stroke", "hospital_social_work", "hospital_ot_discharge",
    ]

    crm_groups: list[tuple[str, list]] = []

    # SOLLA as its own top-level category (source == "solla", regardless of org_type)
    solla_leads = [l for l in leads if l.get("source") == "solla"]
    if solla_leads:
        crm_groups.append(("SOLLA Care Fees IFAs", solla_leads))

    solla_ids = {l["id"] for l in solla_leads}

    for cat_label, org_types in ORG_CATEGORY_OPTIONS.items():
        if cat_label == "IFAs":
            # IFAs that are NOT SOLLA
            cat_leads = [l for l in leads
                         if l["org_type"] in org_types and l["id"] not in solla_ids]
        else:
            cat_leads = [l for l in leads if l["org_type"] in org_types]
        if cat_leads:
            crm_groups.append((cat_label, cat_leads))

    # Anything that fell through (unknown org types)
    categorised = {l["id"] for _, grp in crm_groups for l in grp}
    orphans = [l for l in leads if l["id"] not in categorised]
    if orphans:
        crm_groups.append(("Other", orphans))

    # ── Render grouped ─────────────────────────────────────────────────────────
    for cat_label, cat_leads in crm_groups:
        new_count = sum(1 for l in cat_leads if l["status"] == "new")
        badge = f" · {new_count} new" if new_count else ""
        header = f"**{cat_label}** ({len(cat_leads)}{badge})"

        with st.expander(header, expanded=False):
            if cat_label == "Hospital departments":
                # Sub-group by hospital type
                sub_map: dict[str, list] = {}
                for lead in cat_leads:
                    sub_map.setdefault(lead["org_type"], []).append(lead)
                for sub_type in [t for t in HOSP_SUB_ORDER if t in sub_map]:
                    sub_leads = sub_map[sub_type]
                    sub_label = ORG_TYPE_LABELS.get(sub_type, sub_type)
                    sub_new = sum(1 for l in sub_leads if l["status"] == "new")
                    sub_badge = f" · {sub_new} new" if sub_new else ""
                    with st.expander(
                        f"*{sub_label}* ({len(sub_leads)}{sub_badge})",
                        expanded=False,
                    ):
                        for lead in sorted(sub_leads, key=lambda x: x["priority_score"], reverse=True):
                            _crm_lead_row(lead)
            else:
                for lead in sorted(cat_leads, key=lambda x: x["priority_score"], reverse=True):
                    _crm_lead_row(lead)


# ── Page: Scoring Weights ─────────────────────────────────────────────────────

elif page == "Scoring Weights":
    st.header("Scoring Weights")
    st.caption(
        "Shows current effective weights per organisation type. "
        "Weights blend static rules (70%) with your empirical conversion rates (30%)."
    )

    from scoring.rules import ORG_TYPE_BASE_SCORES, WEALTH_INDICATOR_SCORES
    counts = queries.get_feedback_counts_by_type()
    db_weights = queries.get_scoring_weights()

    rows = []
    for org_type, static_weight in ORG_TYPE_BASE_SCORES.items():
        feedback = counts.get(org_type, {"contacted": 0, "converted": 0})
        contacted = feedback["contacted"]
        converted = feedback["converted"]
        conv_rate = converted / max(contacted, 1) if contacted else 0.0
        effective = db_weights.get(org_type, {}).get("base_weight", static_weight)
        wealth = WEALTH_INDICATOR_SCORES.get(org_type, 0.3)
        rows.append({
            "Org Type": ORG_TYPE_LABELS.get(org_type, org_type),
            "Referral Priority": f"{static_weight:.2f}",
            "Wealth Indicator": f"{wealth:.2f}",
            "Contacted": contacted,
            "Converted": converted,
            "Conv. Rate": f"{conv_rate:.0%}",
            "Effective Weight": f"{effective:.2f}",
        })

    st.dataframe(rows, use_container_width=True)

    st.divider()
    st.markdown("""
    **How weights work:**
    - **Static Base** — initial priority from rules (GP surgeries score highest as primary referral source)
    - **Effective Weight** — blended score after feedback: `(static × 0.7) + (conversion_rate × 0.3)`
    - As you log outcomes, types with high conversion rates rise; poor-performing types fall
    - Click **Re-score** on the Lead Dashboard to apply updated weights to open leads
    """)


# ── Page: Instructions ────────────────────────────────────────────────────────

elif page == "Instructions":
    st.header("How to Use This Tool")
    st.markdown(
        "This tool finds and prioritises organisations near your care home that are "
        "likely to refer residents to you — GPs, hospitals, solicitors, IFAs, "
        "community groups and more."
    )

    st.subheader("1. Run a Search")
    st.markdown("""
    Go to **New Search** and enter:
    - **Care Home Name** — used to label results and group your CRM history
    - **Postcode** — the postcode of your care home
    - **Radius (km)** — how far out to search (5 km is a good starting point)

    Choose your **data sources**. NHS + OpenStreetMap + Web/Social covers most organisations.
    Companies House adds solicitors, IFAs, and estate agents. SOLLA adds specialist care fees advisers.

    Under **Organisation filters & enrichment** you can narrow categories (e.g. hospitals only)
    or enable LinkedIn enrichment to find named contacts (adds 2–3 minutes).

    Click **Run Search** — results are saved automatically.
    """)

    st.subheader("2. Review Results")
    st.markdown("""
    **Lead Dashboard** shows all organisations found, sorted by priority score.
    - Switch between *By org type* (grouped) and *By score* (flat ranked list)
    - Filter by org type, status, or minimum score
    - Each card shows address, phone, website, named contacts, and score breakdown
    - Click **Generate HTML Report** to export a shareable report

    **Map View** plots all leads on an interactive map.
    - Click any marker for a popup, then click again for the full lead card below the map
    - Filter by category, status, or score — the map updates live
    """)

    st.subheader("3. Work Your Leads")
    st.markdown("""
    **Feedback / CRM** is your outreach tracker.
    - Set status: *Contacted*, *Converted*, *Not Converted*, or *Ignored*
    - Add notes (e.g. who you spoke to, outcome, follow-up date)
    - Status and notes carry forward automatically when you re-run a search for the same care home

    Use **Re-score** on the Lead Dashboard after logging outcomes to re-rank leads based on
    your real-world conversion rates.
    """)

    st.subheader("4. Priority Scores")
    st.markdown("""
    Every lead gets a score from 0–100 based on four factors:

    | Factor | Weight | What it means |
    |---|---|---|
    | Org type | 40% | How likely this type of org refers residents |
    | Wealth indicator | 25% | How likely their clients can fund private care |
    | Distance | 25% | Closer orgs score higher |
    | Data completeness | 10% | Bonus for having phone/website/contacts |

    Scores update as you log feedback — types with high conversion rates rise automatically.
    """)

    st.subheader("5. Data Sources")
    st.markdown("""
    | Source | What it finds | Notes |
    |---|---|---|
    | NHS (GPs, hospitals, PCNs) | GP surgeries, NHS Trusts, Primary Care Networks | Free, no key needed |
    | OpenStreetMap | Hospitals (7 dept types), hospices, pharmacies, community orgs | Free |
    | Companies House | Solicitors, IFAs, wealth managers, estate agents | Needs API key in .env |
    | Web / Social | Dementia cafes, Age UK, carers groups, day centres + named contacts | Uses DuckDuckGo |
    | SOLLA | SOLLA-accredited care fees IFA specialists | Uses DDG + LinkedIn |
    """)

    st.subheader("Tips")
    st.markdown("""
    - **Start small** — 5 km, NHS + OSM + Web/Social. Add more sources once you know what's useful.
    - **Re-run monthly** — new orgs appear and contacts change. CRM notes carry forward.
    - **Bookmark your URL** — your login persists in the URL. Bookmark it to return without logging in.
    - **Hospital departments** — each hospital generates up to 7 separate leads (discharge team,
      CHC team, frailty unit, etc.). Use the hospital dept filter to pick only the ones relevant to you.
    - **SOLLA IFAs** are the highest-value professional referrers for self-funders. Always include them.
    """)


# ── Page: Admin ───────────────────────────────────────────────────────────────

elif page == "Admin":
    if not _current_user.get("is_admin"):
        st.error("Access denied.")
        st.stop()

    st.header("Admin")

    # ── Users ─────────────────────────────────────────────────────────────────
    st.subheader("Users")
    users = _auth.get_all_users()

    for u in users:
        col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
        with col1:
            st.write(f"**{u['username']}**")
        with col2:
            st.write("Admin" if u["is_admin"] else "User")
        with col3:
            st.write(f"{u['search_count']} searches")
        with col4:
            last = (u["last_search"] or "—")[:10]
            st.write(last)
        with col5:
            if u["id"] != _current_user["id"]:
                new_admin = not u["is_admin"]
                label = "Revoke admin" if u["is_admin"] else "Make admin"
                if st.button(label, key=f"adm_tog_{u['id']}"):
                    _auth.set_admin(u["id"], new_admin)
                    st.rerun()
            else:
                st.write("(you)")

    # ── All Searches ───────────────────────────────────────────────────────────
    st.divider()
    st.subheader("All Searches")
    all_runs = queries.get_all_search_runs_with_users()
    if not all_runs:
        st.info("No searches yet.")
    else:
        table_rows = [
            {
                "User": r.get("username") or "—",
                "Care Home": r["care_home_name"],
                "Postcode": r["postcode"],
                "Radius (km)": r["radius_km"],
                "Leads": r["lead_count"],
                "Date": (r["run_at"] or "")[:10],
                "Run #": r["id"],
            }
            for r in all_runs
        ]
        st.dataframe(table_rows, use_container_width=True)

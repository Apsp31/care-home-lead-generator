"""Care Home Lead Generator — Streamlit app."""
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from db.schema import init_db
from db import queries
from sources.geocoder import postcode_to_latlon
from sources.nhs_ods import NHSODSSource
from sources.overpass import OverpassSource
from sources.companies_house import CompaniesHouseSource
from sources.web_search import WebSearchSource
from scoring.engine import score_org, get_feedback_weights, recalculate_scores_for_run
from scoring.rules import QUALIFICATION_NOTES
from reports.html_report import generate_report, ORG_TYPE_LABELS

init_db()

st.set_page_config(
    page_title="Care Home Lead Generator",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar navigation ────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Lead Generator")
    st.caption("Care home referral outreach")
    st.divider()
    page = st.radio(
        "Navigate",
        ["New Search", "Lead Dashboard", "Feedback / CRM", "Scoring Weights"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Set COMPANIES_HOUSE_API_KEY in .env to enable Companies House data.")


# ── Helpers ───────────────────────────────────────────────────────────────────

STATUS_OPTIONS = ["new", "contacted", "converted", "not_converted", "ignored"]
STATUS_COLOURS = {
    "new": "blue",
    "contacted": "orange",
    "converted": "green",
    "not_converted": "red",
    "ignored": "gray",
}


def status_badge(status: str) -> str:
    colour = STATUS_COLOURS.get(status, "gray")
    return f":{colour}[{status.replace('_', ' ').upper()}]"


def run_sources(lat, lon, radius_km, selected_sources):
    sources = []
    if "NHS (GPs, hospitals, PCNs)" in selected_sources:
        sources.append(NHSODSSource())
    if "OpenStreetMap (hospices, pharmacies, community)" in selected_sources:
        sources.append(OverpassSource())
    if "Companies House (solicitors, estate agents)" in selected_sources:
        sources.append(CompaniesHouseSource())
    if "Web / Social (dementia cafes, LinkedIn, Facebook)" in selected_sources:
        sources.append(WebSearchSource())

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


# ── Page: New Search ──────────────────────────────────────────────────────────

ALL_SOURCES = [
    "NHS (GPs, hospitals, PCNs)",
    "OpenStreetMap (hospices, pharmacies, community)",
    "Companies House (solicitors, estate agents)",
    "Web / Social (dementia cafes, LinkedIn, Facebook)",
]
DEFAULT_SOURCES = [
    "NHS (GPs, hospitals, PCNs)",
    "OpenStreetMap (hospices, pharmacies, community)",
    "Web / Social (dementia cafes, LinkedIn, Facebook)",
]

if page == "New Search":
    st.header("New Lead Search")

    # Previous search selector — pre-fills the form below
    prev_runs = queries.get_distinct_care_homes()
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

            feedback_weights = get_feedback_weights()
            run_id = queries.create_search_run(care_home, postcode, radius, lat, lon, selected_sources)

            progress = st.progress(0, text="Querying data sources...")
            with st.spinner("Fetching leads from data sources (this may take 30–60 seconds)..."):
                orgs, errors = run_sources(lat, lon, radius, selected_sources)

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

    runs = queries.get_all_search_runs()
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
            default=[],
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
            b64 = base64.b64encode(html.encode()).decode()
            href = f'<a href="data:text/html;base64,{b64}" download="leads_{run_id}.html" target="_blank">Click to download report</a>'
            st.markdown(href, unsafe_allow_html=True)
    with col_b:
        if st.button("Re-score (apply feedback)"):
            recalculate_scores_for_run(run_id, run["radius_km"])
            st.success("Scores updated from feedback data.")
            st.rerun()

    from urllib.parse import quote_plus
    from reports.html_report import TYPE_ORDER

    def _social_links(name: str, town: str) -> str:
        q = quote_plus(f"{name} {town}".strip())
        li = f"https://www.linkedin.com/search/results/companies/?keywords={q}"
        fb = f"https://www.facebook.com/search/top?q={q}"
        return f"[LinkedIn]({li}) · [Facebook]({fb})"

    def _render_contacts(contacts: list[dict]):
        """
        Show contacts that have real names/details in full.
        Collapse pure placeholder rows (no name, no email, no phone) into
        a single 'Look for: Role1 · Role2' hint line.
        """
        real = [c for c in contacts
                if c.get("name") or c.get("email") or c.get("phone")]
        placeholders = [c for c in contacts
                        if not c.get("name") and not c.get("email") and not c.get("phone")]

        for c in real:
            if c.get("name"):
                st.markdown(f"- **{c['name']}** — {c['role']}")
            else:
                st.markdown(f"- _{c['role']}_")
            if c.get("email"):
                st.markdown(f"  - Email: {c['email']}")
            if c.get("phone"):
                st.markdown(f"  - Phone: {c['phone']}")
            note = c.get("source_notes", "")
            if note and note not in ("Role placeholder",):
                st.caption(f"  {note}")

        if placeholders:
            roles = " · ".join(c["role"] for c in placeholders)
            prefix = "Also look for:" if real else "If no named contact found, look for:"
            st.caption(f"{prefix} {roles}")

        if not real and not placeholders:
            st.caption("No contacts on record — use Find online links to locate.")

    def _render_lead_card(lead: dict, show_qual_note: bool = True):
        score = lead["priority_score"]
        sc = "green" if score >= 0.7 else ("orange" if score >= 0.4 else "red")
        contacts = queries.get_contacts_for_org(lead["org_id"])

        with st.expander(
            f"**{lead['name']}** | :{sc}[{int(score*100)}] | "
            f"{ORG_TYPE_LABELS.get(lead['org_type'], lead['org_type'])} | "
            f"{lead['distance_km'] or '?'} km | {status_badge(lead['status'])}",
            expanded=False,
        ):
            if show_qual_note:
                qual = QUALIFICATION_NOTES.get(lead["org_type"], "")
                if qual:
                    st.info(qual)

            c1, c2 = st.columns(2)
            with c1:
                parts = [lead.get("address_line1"), lead.get("address_line2"),
                         lead.get("town"), lead.get("postcode")]
                addr = ", ".join(p for p in parts if p)
                st.markdown(f"**Address:** {addr or '—'}")
                if lead.get("phone"):
                    st.markdown(f"**Phone:** {lead['phone']}")
                if lead.get("email"):
                    st.markdown(f"**Email:** {lead['email']}")
                if lead.get("website"):
                    st.markdown(f"**Website:** {lead['website']}")
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
                st.markdown(f"**Notes:** {lead['notes']}")

    def _render_hospital_group(parent_name: str, depts: list, show_dept_qual: bool = True):
        best = max(depts, key=lambda x: x["priority_score"])
        sc = "green" if best["priority_score"] >= 0.7 else (
            "orange" if best["priority_score"] >= 0.4 else "red")
        with st.expander(
            f"🏥 **{parent_name}** | :{sc}[{int(best['priority_score']*100)}] | "
            f"{len(depts)} departments | {best['distance_km'] or '?'} km",
            expanded=False,
        ):
            d = depts[0]
            parts = [d.get("address_line1"), d.get("address_line2"),
                     d.get("town"), d.get("postcode")]
            addr = ", ".join(p for p in parts if p)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Address:** {addr or '—'}")
                if d.get("phone"):
                    st.markdown(f"**Switchboard:** {d['phone']}")
                if d.get("email"):
                    st.markdown(f"**Email:** {d['email']}")
                if d.get("website"):
                    st.markdown(f"**Website:** {d['website']}")
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
                with st.expander(
                    f"**{dept_label}** | :{sc2}[{int(score*100)}] | {status_badge(dept['status'])}",
                    expanded=False,
                ):
                    if show_dept_qual:
                        qual = QUALIFICATION_NOTES.get(dept["org_type"], "")
                        if qual:
                            st.info(qual)
                    _render_contacts(dept_contacts)
                    if dept.get("notes"):
                        st.markdown(f"**Notes:** {dept['notes']}")

    # ── View mode toggle ───────────────────────────────────────────────────────
    view_mode = st.radio(
        "Group by",
        ["By score", "By org type"],
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

    def _group_score(lst):
        return max(l["priority_score"] for l in lst)

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
        # Build type → leads mapping (non-hospital)
        type_map: dict[str, list] = {}
        for lead in other_leads:
            type_map.setdefault(lead["org_type"], []).append(lead)

        # Hospital types → one virtual type bucket per parent hospital group
        if hosp_groups:
            type_map["_hospitals"] = list(hosp_groups.items())  # list of (parent, depts)

        # Render in priority order
        ordered_types = [t for t in TYPE_ORDER if t in type_map or t == "_hospitals"]
        # Tack on any types not in TYPE_ORDER
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
            else:
                label = ORG_TYPE_LABELS.get(org_type, org_type.replace("_", " ").title())
                qual = QUALIFICATION_NOTES.get(org_type, "")

            n = len(items) if org_type != "_hospitals" else sum(len(d) for _, d in items)
            st.markdown(f"### {label} ({n})")
            if qual:
                st.info(qual)

            if org_type == "_hospitals":
                for parent_name, depts in sorted(items, key=lambda x: _group_score(x[1]), reverse=True):
                    _render_hospital_group(parent_name, depts, show_dept_qual=False)
            else:
                for lead in sorted(items, key=lambda x: x["priority_score"], reverse=True):
                    _render_lead_card(lead, show_qual_note=False)

            st.markdown("")  # spacer between sections


# ── Page: Feedback / CRM ──────────────────────────────────────────────────────

elif page == "Feedback / CRM":
    st.header("Feedback & CRM")
    st.caption("Update lead status and notes. Feedback is used to re-weight prioritisation.")

    runs = queries.get_all_search_runs()
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

    for lead in leads:
        contacts = queries.get_contacts_for_org(lead["org_id"])

        with st.expander(
            f"**{lead['name']}** | {ORG_TYPE_LABELS.get(lead['org_type'], lead['org_type'])} | "
            f"{status_badge(lead['status'])}",
            expanded=(lead["status"] == "new"),
        ):
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown("**Key contacts:**")
                for c in contacts:
                    if c.get("name"):
                        st.markdown(f"- **{c['name']}** — {c['role']}")
                    else:
                        st.markdown(f"- _{c['role']}_")
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

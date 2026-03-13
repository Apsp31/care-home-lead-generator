"""Static base scores, wealth indicators, and qualification notes per organisation type."""

# Base type priority (volume/likelihood of referral)
ORG_TYPE_BASE_SCORES: dict[str, float] = {
    # Hospital departments — each is a separate lead
    "hospital_private":     0.95,
    "hospital_discharge":   0.95,
    "hospital_chc":         0.90,  # CHC assessors identify self-funders by definition
    "hospital_frailty":     0.90,
    "hospital_dementia":    0.90,
    "hospital_ortho":       0.85,
    "hospital_social_work": 0.85,
    "hospital_ot_discharge":0.75,
    "hospital_stroke":      0.80,
    # Other clinical
    "GP":                   0.90,
    "PCN":                  0.75,
    "hospice":              0.80,
    "pharmacy":             0.55,
    # Professional referrers — financial/legal
    "solicitor":            0.90,
    "wealth_manager":       0.90,
    "financial_adviser":    0.85,
    # Property
    "estate_agent":         0.55,
    # Statutory
    "social_services":      0.80,
    # Community — specialist
    "dementia_cafe":        0.85,  # families actively seeking/planning care
    "age_uk_branch":        0.80,  # trusted advisor, direct signposting
    "carers_group":         0.75,  # carers at crisis point — high conversion
    "day_centre":           0.65,  # observes decline; trusted next-step advisor
    # Community — general
    "community_group":      0.35,
    "place_of_worship":     0.25,
    # Other care sector
    "nursing_home":         0.30,
    "domiciliary_care":     0.70,  # clients receiving home care; may transition to residential
    "care_referral":        0.85,  # placement advisers whose job is matching people to care homes
}

# Wealth indicator: likelihood that this org's clients can self-fund ~£2k/week
WEALTH_INDICATOR_SCORES: dict[str, float] = {
    "hospital_private":     1.00,  # explicitly paying privately
    "solicitor":            0.95,  # wills/PoA/probate — asset-rich elderly clients
    "wealth_manager":       0.95,  # HNW asset management
    "financial_adviser":    0.90,  # care fees planning specialists
    "hospital_ortho":       0.85,  # hip/knee patients — often asset-rich elderly
    "hospital_frailty":     0.80,
    "hospital_dementia":    0.80,
    "hospital_discharge":   0.75,  # high volume, mixed funding
    "hospital_chc":         0.80,  # CHC ruling-out directly surfaces self-funders
    "hospital_stroke":      0.75,
    "hospital_social_work": 0.65,  # identifies self-funders above £23,250 threshold
    "hospital_ot_discharge":0.60,
    "estate_agent":         0.75,  # selling home to fund care
    "GP":                   0.65,
    "hospice":              0.70,
    "PCN":                  0.60,
    "social_services":      0.60,
    "pharmacy":             0.35,
    "dementia_cafe":        0.85,  # families are self-funders planning specialist care
    "age_uk_branch":        0.60,  # mixed wealth, but high trust and referral intent
    "carers_group":         0.65,  # carers often managing estate / property decisions
    "day_centre":           0.45,
    "community_group":      0.20,
    "place_of_worship":     0.15,
    "nursing_home":         0.25,
    "domiciliary_care":     0.55,  # some self-funders; many LA-funded but declining clients
    "care_referral":        0.80,  # clients actively seeking placement; often self-funders
}

# Displayed in the report and dashboard to explain relevance
QUALIFICATION_NOTES: dict[str, str] = {
    "hospital_private":
        "Private Patient Unit — patients already paying privately; coordinator manages post-discharge "
        "placement and is the most direct route to self-funding residents.",
    "hospital_discharge":
        "Discharge Planning / Transfer of Care — primary route for post-hospital residential placements. "
        "Team coordinates care packages and placements; self-funders routinely signposted to private homes.",
    "hospital_frailty":
        "Frailty & Elderly Care Unit — specialist geriatric patients transitioning to long-term care. "
        "Consultant and nurse practitioner are key influencers for private placement decisions.",
    "hospital_dementia":
        "Memory Clinic / Dementia Service — families actively seeking specialist residential placements; "
        "high conversion as dementia care is typically long-term and privately funded.",
    "hospital_ortho":
        "Trauma & Orthopaedics — post-hip/knee surgery patients are predominantly 70+ and asset-rich. "
        "Liaison nurse coordinates step-down and longer-term residential care.",
    "hospital_stroke":
        "Stroke Rehabilitation Unit — ongoing care needs post-discharge; many patients fund care through "
        "property assets. Rehabilitation coordinator shapes placement pathway.",
    "hospital_chc":
        "NHS Continuing Healthcare Team — assesses every complex discharge patient for NHS-funded nursing "
        "care. Patients who do not qualify (the majority) become self-funders by default. CHC coordinators "
        "and nurse assessors are at the exact point where the funding question is resolved.",
    "hospital_ot_discharge":
        "Hospital Discharge Occupational Therapist — conducts functional assessments that determine whether "
        "a patient can return home or requires care home placement. More accessible than social workers; "
        "directly gates the placement pathway for post-acute patients.",
    "hospital_social_work":
        "Hospital Social Work Department — identifies and assesses self-funders (assets above £23,250 "
        "threshold). Principal social worker is a gatekeeper for private residential placements.",
    "solicitor":
        "Private Client Solicitor — handles Wills, Lasting Powers of Attorney and Probate for asset-rich "
        "elderly clients. Positioned to recommend care providers at the moment of greatest need.",
    "wealth_manager":
        "Wealth / Fund Manager — manages assets of HNW individuals and can advise on care cost planning. "
        "Relationship managers often facilitate introductions to quality care providers.",
    "financial_adviser":
        "Independent Financial Adviser — SOLLA-accredited IFAs specialise in care fees planning. "
        "Directly introduce clients who are planning or in immediate need of residential care.",
    "GP":
        "GP Practice — primary referral source for care assessments. Patient list includes elderly with "
        "assets who will self-fund rather than wait for local authority funding.",
    "PCN":
        "Primary Care Network — engaging the Clinical Director reaches multiple GP practices simultaneously. "
        "PCN-level relationships have broad referral reach.",
    "hospice":
        "Hospice / Palliative Care — professionals regularly signpost families to residential care for "
        "step-down or longer-term placement; strong trust relationship aids referral.",
    "pharmacy":
        "Community Pharmacy — often first to identify declining elderly patients. Trusted by families "
        "and able to make warm referrals; lower direct conversion but valuable touchpoint.",
    "estate_agent":
        "Estate Agent — selling the family home to fund care is one of the most common funding routes. "
        "A later-living or downsizing specialist can refer clients at the moment of decision.",
    "social_services":
        "Adult Social Care — statutory assessments identify self-funders above the £23,250 asset threshold "
        "who must arrange and fund their own care. Team manager is key relationship.",
    "dementia_cafe":
        "Dementia Cafe / Memory Cafe — families and carers attending are actively living with or planning "
        "for dementia care. Coordinators are trusted community figures who signpost residential options. "
        "High conversion: attendees are typically at the point of need.",
    "age_uk_branch":
        "Age UK / Age Concern Branch — the most trusted voluntary sector advisor for older people. "
        "Information & Advice Officers routinely signpost families to care homes. Strong referral relationship.",
    "carers_group":
        "Carers Support Group — family carers attending are often managing at-home care that is becoming "
        "unsustainable. Coordinators are key influencers at the crisis point when residential care is sought.",
    "day_centre":
        "Elderly Day Centre — staff observe clients' daily decline and are trusted by families for "
        "'what's next' advice. Centre manager is the key relationship for warm referrals.",
    "community_group":
        "Community / Voluntary Group — lower direct referral value but useful for brand visibility and "
        "word-of-mouth among active older adults and carers.",
    "place_of_worship":
        "Place of Worship — pastoral networks reach isolated elderly; longer-term relationship building "
        "rather than direct referrals. Lower immediate ROI.",
    "nursing_home":
        "Other Care Home — peer networking opportunity; cross-referrals when at capacity or for specialist "
        "dementia / nursing needs outside their registration.",
    "domiciliary_care":
        "Domiciliary Care Agency — provides care at home to elderly clients who may deteriorate and require "
        "residential placement. Care managers observe decline daily and are trusted by families. "
        "A relationship here generates referrals at the point home care is no longer sufficient.",
    "care_referral":
        "Care Placement Adviser / Referral Agency — their specific role is matching people to care homes. "
        "Clients are actively seeking placement, often self-funding, and have an immediate need. "
        "These are among the highest-value referral relationships available.",
}

# Component weights — must sum to 1.0
COMPONENT_WEIGHTS = {
    "type_score":       0.40,
    "wealth_indicator": 0.25,
    "distance_score":   0.25,
    "completeness":     0.10,
}

DEFAULT_BASE_SCORE = 0.35
DEFAULT_WEALTH_SCORE = 0.30

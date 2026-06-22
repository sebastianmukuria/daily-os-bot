"""
Generate SYNTHETIC job-search data for the public Evidence demo. Entirely fake —
no real companies, roles, or outcomes. Deterministic (fixed seed) so the demo and
README screenshot are reproducible. Run from the evidence/ dir: python3 generate.py
"""

import csv
import random
from datetime import date, timedelta

random.seed(7)

COMPANIES = [
    "Helio Analytics", "Northwind Data", "Cobalt Systems", "Meridian Labs",
    "Lumen Insights", "Beacon Metrics", "Aster Technologies", "Vantage BI",
    "Quanta Group", "Tidewater Analytics", "Ironwood Software", "Polaris Data",
    "Cedar & Co", "Halcyon Health", "Brightline Energy", "Summit Logistics",
    "Greylock Retail", "Marigold Media", "Onyx Financial", "Driftwood Labs",
    "Sable Robotics", "Verdant Foods", "Atlas Mobility", "Crescent Bank",
    "Kestrel Insurance", "Nimbus Cloud", "Granite Ventures", "Foundry Analytics",
]
ROLES = ["Data Analyst", "Business Analyst", "Analytics Engineer", "BI Analyst",
         "Data Analyst II", "Marketing Analyst", "Product Analyst"]
SOURCES = ["LinkedIn", "Direct", "Referral", "Recruiter inbound"]
LOCATIONS = ["Remote", "Hybrid", "SoCal", "Relocation"]
STAGE_RANK = {"Applied": 1, "Recruiter Screen": 2, "Interviewing": 3, "Final Round": 4, "Offer": 5}

# (furthest_stage, current_status, count) — shapes a realistic funnel.
PROFILE = [
    ("Applied", "Applied", 14),
    ("Applied", "Ghosted", 9),
    ("Applied", "Rejected", 7),
    ("Recruiter Screen", "Recruiter Screen", 4),
    ("Recruiter Screen", "Rejected", 5),
    ("Recruiter Screen", "Ghosted", 1),
    ("Interviewing", "Interviewing", 2),
    ("Interviewing", "Rejected", 3),
    ("Final Round", "Final Round", 1),
    ("Final Round", "Rejected", 1),
    ("Offer", "Offer", 1),
]

rows = []
start = date(2026, 3, 15)
companies = COMPANIES.copy()
random.shuffle(companies)
ci = 0
for furthest, status, n in PROFILE:
    for _ in range(n):
        applied = start + timedelta(days=random.randint(0, 95))
        rows.append({
            "company": companies[ci % len(companies)],
            "role": random.choice(ROLES),
            "status": status,
            "furthest_stage": furthest,
            "applied_date": applied.isoformat(),
            "source": random.choice(SOURCES),
            "location": random.choice(LOCATIONS),
        })
        ci += 1

rows.sort(key=lambda r: r["applied_date"])
with open("sources/jobsearch/applications.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["company", "role", "status", "furthest_stage",
                                      "applied_date", "source", "location"])
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} synthetic applications -> applications.csv")

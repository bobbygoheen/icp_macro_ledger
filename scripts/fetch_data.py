#!/usr/bin/env python3
"""
Fetches macro data from the St. Louis Fed FRED API and writes data.json
for The ICP Macro Ledger. Runs on a schedule via GitHub Actions.

Requires env var FRED_API_KEY (free key from https://fred.stlouisfed.org/docs/api/api_key.html)
No third-party dependencies — standard library only.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, timedelta

API_KEY = os.environ.get("FRED_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FRED_API_KEY environment variable is not set.", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.stlouisfed.org/fred/series/observations"
START = (date.today() - timedelta(days=5 * 365 + 30)).isoformat()  # ~5 years back

# key -> (FRED series id, transform)
# transform: "level" = use raw values; "yoy" = 12-month percent change
SERIES = {
    "wti":          ("DCOILWTICO",       "level"),  # WTI crude, $/bbl, daily
    "brent":        ("DCOILBRENTEU",     "level"),  # Brent crude, $/bbl, daily
    "ust10y":       ("DGS10",            "level"),  # 10Y Treasury yield, %, daily
    "ust2y":        ("DGS2",             "level"),  # 2Y Treasury yield, %, daily
    "curve":        ("T10Y2Y",           "level"),  # 10Y minus 2Y spread, %, daily
    "hyoas":        ("BAMLH0A0HYM2",     "level"),  # ICE BofA HY OAS, %, daily
    "igoas":        ("BAMLC0A0CM",       "level"),  # ICE BofA IG Corp OAS, %, daily
    "fedfunds":     ("DFEDTARU",         "level"),  # Fed funds target upper bound, %, daily
    "cpiHeadline":  ("CPIAUCSL",         "yoy"),    # CPI, index -> YoY %
    "cpiCore":      ("CPILFESL",         "yoy"),    # Core CPI, index -> YoY %
    "pceHeadline":  ("PCEPI",            "yoy"),    # PCE price index -> YoY %
    "pceCore":      ("PCEPILFE",         "yoy"),    # Core PCE price index -> YoY %
    "ppi":          ("PPIFIS",           "yoy"),    # PPI final demand -> YoY %
    "unemployment": ("UNRATE",           "level"),  # Unemployment rate, %, monthly
    "payrolls":     ("PAYEMS",           "diff"),   # Nonfarm payrolls level -> monthly change (K)
    "ahe":          ("CES0500000003",    "yoy"),    # Avg hourly earnings -> YoY %
    "claims":       ("ICSA",             "level"),  # Initial claims, weekly (persons)
    "umich":        ("UMCSENT",          "level"),  # UMich sentiment, monthly
    "saving":       ("PSAVERT",          "level"),  # Personal saving rate, %, monthly
    "mortgage30y":  ("MORTGAGE30US",     "level"),  # 30Y mortgage rate, %, weekly
    "housingStarts":("HOUST",            "level"),  # Housing starts, thousands SAAR, monthly
    "existingHome": ("EXHOSLUSM495S",    "level"),  # Existing home sales, SAAR, monthly (may be restricted; skipped if so)
    "gdpnow":       ("GDPNOW",           "level"),  # Atlanta Fed GDPNow, %, ~weekly
    "gdp":          ("A191RL1Q225SBEA",  "level"),  # Real GDP QoQ SAAR %, quarterly
    "ismMfg":       ("NAPM",             "level"),  # ISM Manufacturing PMI (may be restricted; skipped if so)
    "vix":          ("VIXCLS",           "level"),  # VIX, daily
}

MAX_POINTS = 90  # downsample long daily histories to keep data.json small


def fetch_series(series_id):
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": API_KEY,
        "file_type": "json",
        "observation_start": START,
    })
    url = f"{BASE}?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.load(resp)
    obs = []
    for o in payload.get("observations", []):
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                obs.append((o["date"], float(v)))
            except ValueError:
                pass
    return obs


def yoy(obs):
    """12-month percent change for monthly index series."""
    by_date = {d: v for d, v in obs}
    out = []
    for d, v in obs:
        y, m, day = d.split("-")
        prior_key = f"{int(y)-1}-{m}-{day}"
        if prior_key in by_date and by_date[prior_key] != 0:
            out.append((d, round((v / by_date[prior_key] - 1) * 100, 2)))
    return out


def diff(obs):
    """Month-over-month change (for payroll levels, already in thousands)."""
    out = []
    for i in range(1, len(obs)):
        out.append((obs[i][0], round(obs[i][1] - obs[i - 1][1], 1)))
    return out


def downsample(obs, max_points=MAX_POINTS):
    if len(obs) <= max_points:
        return obs
    step = len(obs) / max_points
    sampled = [obs[int(i * step)] for i in range(max_points)]
    if sampled[-1][0] != obs[-1][0]:
        sampled.append(obs[-1])
    return sampled


def main():
    result = {"updated": date.today().isoformat(), "series": {}}
    failures = []

    for key, (sid, transform) in SERIES.items():
        try:
            obs = fetch_series(sid)
            if transform == "yoy":
                obs = yoy(obs)
            elif transform == "diff":
                obs = diff(obs)
            if len(obs) < 2:
                raise ValueError("insufficient observations")
            obs = downsample(obs)
            latest_date, latest = obs[-1]
            prev = obs[-2][1]
            result["series"][key] = {
                "latest": latest,
                "prev": prev,
                "latestDate": latest_date,
                "dates": [d for d, _ in obs],
                "values": [v for _, v in obs],
            }
            print(f"OK   {key:14s} {sid:s} -> {latest} ({latest_date})")
        except Exception as e:
            failures.append((key, sid, str(e)))
            print(f"SKIP {key:14s} {sid}: {e}")

    out_path = os.path.join(os.path.dirname(__file__), "..", "data.json")
    with open(out_path, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    print(f"\nWrote data.json with {len(result['series'])} series "
          f"({len(failures)} skipped).")

    # Fail the workflow only if we got almost nothing — partial data is fine.
    if len(result["series"]) < 5:
        print("ERROR: too few series succeeded; failing so the last good data.json is kept.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

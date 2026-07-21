#!/usr/bin/env python3
"""
Generates the non-static layer of The ICP Macro Ledger and merges it into
data.json. Runs after fetch_data.py in the scheduled GitHub Action.

Two modes:
  1. Rule-based (always runs, free): writes data-driven takeaways computed
     directly from the FRED series, plus next-release dates computed from
     known schedules/rules.
  2. Claude-enhanced (optional): if ANTHROPIC_API_KEY is set as a repo
     secret, asks Claude (with web search) to write richer commentary, fetch
     the handful of values FRED doesn't carry (ECB rate, China PMI, ISM
     Services, retail sales), verify release dates, and update the three
     qualitative theme cards. Falls back to rule-based output on any failure.

Standard library only.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date, timedelta

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data.json")
TODAY = date.today()


# ----------------------------------------------------------------------
# Release-date computation
# ----------------------------------------------------------------------

# FOMC 2026 decision days (second day of each scheduled meeting).
FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
             "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]

# ECB 2026 monetary policy decision dates (approximate published calendar).
ECB_2026 = ["2026-01-29", "2026-03-12", "2026-04-30", "2026-06-11",
            "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17"]


def first_weekday_of_month(year, month, weekday):
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset)


def nth_business_day(year, month, n):
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def next_occurrence(candidates):
    for c in candidates:
        d = date.fromisoformat(c) if isinstance(c, str) else c
        if d > TODAY:
            return d
    return None


def approx_monthly(day_of_month, months_ahead=3):
    """Next occurrence of an approximately-fixed monthly release day."""
    out = []
    y, m = TODAY.year, TODAY.month
    for _ in range(months_ahead + 1):
        try:
            d = date(y, m, day_of_month)
        except ValueError:
            d = date(y, m, 28)
        while d.weekday() >= 5:  # roll weekend to Monday
            d += timedelta(days=1)
        out.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return next_occurrence(out)


def fmt(d, est=False):
    if d is None:
        return None
    s = d.strftime("%b %-d") if os.name != "nt" else d.strftime("%b %d").replace(" 0", " ")
    return f"{s} (est.)" if est else s


def compute_releases():
    y, m = TODAY.year, TODAY.month
    nm_y, nm_m = (y, m + 1) if m < 12 else (y + 1, 1)

    jobs_candidates = [first_weekday_of_month(y, m, 4), first_weekday_of_month(nm_y, nm_m, 4)]
    ism_mfg_candidates = [nth_business_day(y, m, 1), nth_business_day(nm_y, nm_m, 1)]
    ism_svc_candidates = [nth_business_day(y, m, 3), nth_business_day(nm_y, nm_m, 3)]

    next_thursday = TODAY + timedelta(days=(3 - TODAY.weekday()) % 7 or 7)

    umich_candidates = []
    for (yy, mm) in [(y, m), (nm_y, nm_m)]:
        first_friday = first_weekday_of_month(yy, mm, 4)
        umich_candidates.append(first_friday + timedelta(days=7))  # 2nd Friday: prelim

    releases = {
        "fomc": fmt(next_occurrence(FOMC_2026)),
        "ecb": fmt(next_occurrence(ECB_2026)),
        "jobs": fmt(next_occurrence(jobs_candidates)),
        "ismMfg": fmt(next_occurrence(ism_mfg_candidates)),
        "ismServices": fmt(next_occurrence(ism_svc_candidates)),
        "claims": "Weekly (Thu)",
        "cpi": fmt(approx_monthly(12), est=True),       # BLS CPI ~2nd week
        "ppi": fmt(approx_monthly(13), est=True),       # PPI ~day after CPI
        "retail": fmt(approx_monthly(15), est=True),    # Census retail ~mid-month
        "housing": fmt(approx_monthly(17), est=True),   # starts ~17th
        "existing": fmt(approx_monthly(21), est=True),  # NAR ~3rd week
        "pce": fmt(approx_monthly(28), est=True),       # BEA PCE ~month-end
        "gdp": fmt(approx_monthly(28), est=True),       # BEA GDP ~month-end
        "umich": fmt(next_occurrence(umich_candidates), est=True),  # prelim ~2nd Friday
        "chinaPMI": fmt(approx_monthly(31), est=True),  # NBS ~end of month
        "daily": "Daily",
        "gdpnowKey": "Updates ~weekly",
    }
    return releases


# Impact tiers for the calendar strip: what actually moves rates/credit.
CALENDAR_SPEC = [
    # (label, impact, key-for-consensus-match, date-source)
    ("FOMC rate decision", "high", "FOMC", ("fomc",)),
    ("Nonfarm payrolls & unemployment", "high", "Payrolls", ("jobs",)),
    ("CPI (headline & core)", "high", "CPI", ("cpi",)),
    ("Core PCE & GDP", "high", "PCE", ("pce",)),
    ("PPI", "med", "PPI", ("ppi",)),
    ("Retail sales", "med", "Retail Sales", ("retail",)),
    ("ISM Manufacturing PMI", "med", "ISM Mfg", ("ismMfg",)),
    ("ISM Services PMI", "med", "ISM Services", ("ismServices",)),
    ("ECB rate decision", "med", "ECB", ("ecb",)),
    ("Housing starts & permits", "med", "Housing Starts", ("housing",)),
    ("Existing home sales", "low", "Existing Home Sales", ("existing",)),
    ("U. Michigan sentiment (prelim)", "low", "UMich", ("umich",)),
]


def build_calendar(horizon_days=21):
    """Chronological list of upcoming releases within the horizon, with impact tags."""
    y, m = TODAY.year, TODAY.month
    nm_y, nm_m = (y, m + 1) if m < 12 else (y + 1, 1)

    date_sources = {
        "fomc": next_occurrence(FOMC_2026),
        "ecb": next_occurrence(ECB_2026),
        "jobs": next_occurrence([first_weekday_of_month(y, m, 4), first_weekday_of_month(nm_y, nm_m, 4)]),
        "ismMfg": next_occurrence([nth_business_day(y, m, 1), nth_business_day(nm_y, nm_m, 1)]),
        "ismServices": next_occurrence([nth_business_day(y, m, 3), nth_business_day(nm_y, nm_m, 3)]),
        "cpi": approx_monthly(12),
        "ppi": approx_monthly(13),
        "retail": approx_monthly(15),
        "housing": approx_monthly(17),
        "existing": approx_monthly(21),
        "pce": approx_monthly(28),
        "umich": next_occurrence([first_weekday_of_month(yy, mm, 4) + timedelta(days=7) for (yy, mm) in [(y, m), (nm_y, nm_m)]]),
    }
    est_keys = {"cpi", "ppi", "retail", "housing", "existing", "pce", "umich"}

    horizon_end = TODAY + timedelta(days=horizon_days)
    items = []
    for label, impact, consensus_key, (src,) in CALENDAR_SPEC:
        d = date_sources.get(src)
        if d and TODAY < d <= horizon_end:
            items.append({
                "date": d.isoformat(),
                "display": fmt(d, est=(src in est_keys)),
                "label": label,
                "impact": impact,
                "consensusKey": consensus_key,
            })
    # Weekly jobless claims: add each Thursday in the horizon (low impact).
    d = TODAY + timedelta(days=(3 - TODAY.weekday()) % 7 or 7)
    while d <= horizon_end:
        items.append({"date": d.isoformat(), "display": fmt(d), "label": "Initial jobless claims",
                      "impact": "low", "consensusKey": "Claims"})
        d += timedelta(days=7)

    items.sort(key=lambda x: x["date"])
    return items


def compute_regime(series):
    """One-line macro-regime read derived from the data (rules-based)."""
    parts = []
    phase = "Mid-cycle"

    hy = get(series, "hyoas")
    if hy:
        r = pct_rank(hy)
        if r is not None:
            if r <= 25:
                parts.append("credit spreads near cycle tights")
                phase = "Late-cycle"
            elif r >= 75:
                parts.append("credit spreads elevated / widening")
                phase = "Stress"
            else:
                parts.append("credit spreads mid-range")

    pay = get(series, "payrolls")
    if pay:
        vals = pay.get("values", [])
        if len(vals) >= 4:
            recent = sum(vals[-2:]) / 2
            earlier = sum(vals[-4:-2]) / 2
            if recent < earlier * 0.7:
                parts.append("labor market cooling")
                if phase == "Late-cycle":
                    phase = "Late-cycle / softening"
            elif recent > earlier * 1.3:
                parts.append("labor market reaccelerating")
            else:
                parts.append("labor market steady")

    core = get(series, "pceCore") or get(series, "cpiCore")
    if core:
        if core["latest"] >= 2.8:
            parts.append(f"core inflation sticky at {core['latest']:.1f}%")
        elif core["latest"] <= 2.3:
            parts.append(f"core inflation near target ({core['latest']:.1f}%)")
        else:
            parts.append(f"core inflation {core['latest']:.1f}%")

    curve = get(series, "curve")
    if curve:
        parts.append("curve inverted" if curve["latest"] < 0 else "curve positively sloped")

    detail = "; ".join(parts) + "." if parts else "Insufficient data for a regime read."
    return {"label": phase, "detail": detail, "source": "rules"}


# ----------------------------------------------------------------------
# Rule-based takeaways from the actual data
# ----------------------------------------------------------------------

def get(series, key):
    s = series.get(key)
    return s if s and s.get("latest") is not None else None


def pct_rank(s):
    """Where the latest value sits in its own ~5y history (0-100)."""
    vals = s.get("values", [])
    if len(vals) < 10:
        return None
    latest = s["latest"]
    below = sum(1 for v in vals if v < latest)
    return round(100 * below / len(vals))


def chg(s, n=1):
    vals = s.get("values", [])
    if len(vals) <= n:
        return None
    return s["latest"] - vals[-1 - n]


def rule_takeaways(series):
    items = []

    wti = get(series, "wti")
    if wti:
        r = pct_rank(wti)
        items.append({"tag": "Energy", "text":
            f"WTI is at ${wti['latest']:.2f}/bbl"
            + (f", higher than {r}% of readings over the past five years" if r is not None else "")
            + ". Oil remains the biggest swing factor for headline inflation and for input costs at transport-, logistics-, and manufacturing-exposed borrowers."})

    pay = get(series, "payrolls")
    un = get(series, "unemployment")
    if pay:
        vals = pay.get("values", [])
        avg3 = sum(vals[-3:]) / min(3, len(vals)) if vals else None
        t = f"Payrolls added {pay['latest']:+.0f}K in the latest report"
        if avg3 is not None:
            t += f" ({avg3:+.0f}K 3-month average)"
        if un:
            t += f", with unemployment at {un['latest']:.1f}%"
        t += ". Hiring momentum is the leading read on demand durability for growth-oriented credits."
        items.append({"tag": "Labor", "text": t})

    cpi = get(series, "cpiHeadline")
    core = get(series, "cpiCore")
    pce = get(series, "pceCore")
    if cpi:
        d = chg(cpi)
        t = f"Headline CPI is running {cpi['latest']:.1f}% YoY"
        if d is not None:
            t += f" ({d:+.1f}pt vs the prior reading)"
        if core:
            t += f"; core CPI {core['latest']:.1f}%"
        if pce:
            t += f"; core PCE — the Fed's target metric — {pce['latest']:.1f}%"
        t += ". The gap between headline volatility (energy) and sticky core is the key tension for the rate path."
        items.append({"tag": "Inflation", "text": t})

    ff = get(series, "fedfunds")
    curve = get(series, "curve")
    if ff:
        t = f"The Fed funds target stands at {ff['latest'] - 0.25:.2f}–{ff['latest']:.2f}%"
        if curve:
            bp = round(curve["latest"] * 100)
            t += f", with the 10Y–2Y curve at {bp:+d}bp ({'normal upward slope' if bp > 0 else 'inverted'})"
        t += ". Base-rate assumptions in every floating-rate underwriting case key off this."
        items.append({"tag": "Policy", "text": t})

    hy = get(series, "hyoas")
    ig = get(series, "igoas")
    if hy:
        r = pct_rank(hy)
        bp = round(hy["latest"] * 100)
        t = f"HY OAS is {bp}bp"
        if r is not None:
            t += f" — tighter than {100 - r}% of the past five years" if r < 50 else f" — wider than {r}% of the past five years"
        if ig:
            t += f"; IG OAS {round(ig['latest'] * 100)}bp"
        t += ". Spread levels this stretched relative to history are the cleanest single gauge of how much credit risk is being paid for."
        items.append({"tag": "Credit", "text": t})

    gdp = get(series, "gdp")
    gnow = get(series, "gdpnow")
    if gdp or gnow:
        t = ""
        if gdp:
            t += f"Real GDP grew {gdp['latest']:.1f}% (QoQ SAAR) in the most recent quarter"
        if gnow:
            t += (" and the " if t else "The ") + f"Atlanta Fed's GDPNow is tracking {gnow['latest']:+.1f}% for the current quarter"
        t += ". Below-trend-but-positive growth remains the base case supporting growth-oriented over distressed positioning."
        items.append({"tag": "Growth", "text": t})

    um = get(series, "umich")
    hs = get(series, "housingStarts")
    mort = get(series, "mortgage30y")
    if um or hs:
        t = ""
        if um:
            r = pct_rank(um)
            t += f"UMich consumer sentiment is {um['latest']:.1f}" + (f", lower than {100 - r}% of the past five years" if r is not None else "")
        if hs:
            t += ("; housing starts are " if t else "Housing starts are ") + f"{hs['latest'] / 1000:.2f}M SAAR"
        if mort:
            t += f" against a {mort['latest']:.2f}% 30Y mortgage rate"
        t += ". The consumer and housing complex is the most rate-sensitive part of the demand picture."
        items.append({"tag": "Consumer & Housing", "text": t})

    return items


# ----------------------------------------------------------------------
# Optional Claude enhancement
# ----------------------------------------------------------------------

CLAUDE_PROMPT_TEMPLATE = """You are writing the daily brief for a macro dashboard used by a special situations credit investor at a large asset manager. Today is {today}.

Here are the latest values from FRED (JSON): {summary}

Do the following, using web search where needed (max 5 searches):
1. Write 6-7 concise takeaways (2-3 sentences each) on what currently matters most across: energy, labor, inflation, policy/rates, credit markets, growth, consumer/housing — grounded in the numbers above plus any major macro news from the past week. Angle everything toward special situations / growth-oriented credit investing. No investment advice, just analysis.
2. Find current values for these series (not in FRED): ECB deposit rate, China official manufacturing PMI, ISM Services PMI, US retail sales (latest m/m %), US existing home sales (millions SAAR).
3. Identify the special situations / market themes MOST MATERIAL to a special-situations credit investor RIGHT NOW, based on current global developments from the past week or two. Pick between 3 and 6 themes — the actual number depends on how much is genuinely material right now, don't pad to hit a target. These are not a fixed list: a theme could be a refinancing wall, a specific sector under stress (e.g. CRE, autos, healthcare), a sovereign or geopolitical event, a commodity or energy shock, a regulatory/legal ruling, a dislocation in a specific credit market, a large idiosyncratic default or restructuring, etc. Choose whatever is actually driving special-situations opportunity or risk today. For each: a short title (3-6 words), a one-word status ("Acute", "Elevated", "Emerging", "Easing", "Live risk"), a directional flag, a 1-2 sentence status line describing the current situation, and a 1-2 sentence note on why it matters specifically for special-situations credit.
4. For the next few high-impact US releases (CPI, Payrolls, Core PCE, PPI, Retail Sales, ISM Mfg, ISM Services, GDP as applicable), give the current market consensus expectation. Use these exact labels as keys: "CPI", "Payrolls", "PCE", "PPI", "Retail Sales", "ISM Mfg", "ISM Services", "GDP".
5. For any of those indicators that printed in the LAST ~10 DAYS, give the actual vs. consensus surprise.
6. Write a one-line macro-regime summary (a phase label plus a short clause), e.g. "Late-cycle — tight spreads, cooling labor, sticky core inflation."

Respond with ONLY this JSON (no markdown fences, no other text):
{{"takeaways":[{{"tag":"Energy","text":"..."}}],
"extraSeries":{{"ecb":{{"value":"N.NN%","delta":"context","dir":"up|down|flat"}},"chinaPMI":{{"value":"NN.N","delta":"context","dir":"up|down|flat"}},"ismServices":{{"value":"NN.N","delta":"context","dir":"up|down|flat"}},"retailSales":{{"value":"+N.N% m/m","delta":"context","dir":"up|down|flat"}},"existingHome":{{"value":"N.NNM","delta":"context","dir":"up|down|flat"}}}},
"themeCards":[{{"title":"3-6 word title","value":"Elevated","dir":"up|down|flat","statusLine":"1-2 sentence current situation","relevance":"1-2 sentences on why it matters for special situations credit"}}],
"consensus":{{"CPI":"3.4% YoY exp","Payrolls":"+80K exp"}},
"surprises":[{{"label":"CPI","actual":"3.5%","consensus":"3.4%","dir":"up"}}],
"regime":{{"label":"Late-cycle","detail":"tight spreads, cooling labor, sticky core inflation"}}}}"""


def claude_enhance(series):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    summary = {}
    for k, s in series.items():
        if s.get("latest") is not None:
            summary[k] = {"latest": s["latest"], "prev": s["prev"], "date": s.get("latestDate")}

    prompt = CLAUDE_PROMPT_TEMPLATE.format(today=TODAY.isoformat(), summary=json.dumps(summary))
    body = json.dumps({
        # Current cost-effective model for this task (verified against
        # docs.claude.com). Haiku 4.5 is the cheapest capable option and
        # plenty for a structured data+news summary run twice a day. Swap to
        # "claude-sonnet-5" for richer commentary at higher cost. Always
        # verify the current string at docs.claude.com before changing.
        "model": "claude-haiku-4-5",
        "max_tokens": 2500,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
        text = "\n".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError("no JSON in response")
        parsed = json.loads(m.group(0))
        if not parsed.get("takeaways"):
            raise ValueError("no takeaways in response")
        return parsed
    except Exception as e:
        print(f"Claude enhancement failed ({e}); using rule-based commentary.", file=sys.stderr)
        return None


# ----------------------------------------------------------------------

def main():
    with open(DATA_PATH) as f:
        data = json.load(f)
    series = data.get("series", {})

    data["releases"] = compute_releases()
    data["calendar"] = build_calendar()

    enhanced = claude_enhance(series)
    if enhanced:
        data["commentary"] = {
            "source": "claude",
            "generated": TODAY.isoformat(),
            "items": enhanced["takeaways"],
        }
        if enhanced.get("extraSeries"):
            data["extraSeries"] = enhanced["extraSeries"]
        tc = enhanced.get("themeCards")
        if isinstance(tc, list) and len(tc) >= 1:
            data["themeCards"] = tc[:6]
        if enhanced.get("consensus"):
            data["consensus"] = enhanced["consensus"]
        if enhanced.get("surprises"):
            data["surprises"] = enhanced["surprises"]
        if enhanced.get("regime"):
            data["regime"] = {**enhanced["regime"], "source": "claude"}
        else:
            data["regime"] = compute_regime(series)
        print(f"Commentary: Claude-enhanced ({len(data.get('themeCards', []))} theme cards, "
              f"{len(data.get('consensus', {}))} consensus, {len(data.get('surprises', []))} surprises).")
    else:
        data["commentary"] = {
            "source": "rules",
            "generated": TODAY.isoformat(),
            "items": rule_takeaways(series),
        }
        data["regime"] = compute_regime(series)
        print("Commentary: rule-based (theme cards & consensus unchanged from last run).")

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"Wrote commentary ({len(data['commentary']['items'])} takeaways), "
          f"calendar ({len(data['calendar'])} events), "
          f"regime ({data['regime']['label']}), "
          f"themeCards ({len(data.get('themeCards', []))}).")


if __name__ == "__main__":
    main()

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


CITE_RE = re.compile(r"</?cite[^>]*>", re.IGNORECASE)
INDEX_RE = re.compile(r"\s*index\s*=\s*[\"'][^\"']*[\"']", re.IGNORECASE)


def strip_citations(text):
    """Remove citation markup that web-search responses can embed in prose.

    The model may wrap cited claims in <cite index="2-1">...</cite>. Those tags
    are meaningless in the dashboard and render as literal text, so strip the
    tags (keeping the sentence inside) before parsing/persisting.
    """
    if not isinstance(text, str):
        return text
    out = CITE_RE.sub("", text)
    out = INDEX_RE.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def scrub(obj):
    """Recursively strip citation markup from every string in a structure."""
    if isinstance(obj, str):
        return strip_citations(obj)
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def trend_word(s, n=3):
    """Direction over the last n observations."""
    vals = s.get("values", [])
    if len(vals) <= n:
        return None
    recent, earlier = vals[-1], vals[-1 - n]
    if earlier == 0:
        return None
    pct = (recent - earlier) / abs(earlier)
    if pct > 0.05:
        return "rising"
    if pct < -0.05:
        return "falling"
    return "roughly flat"


def rule_takeaways(series):
    items = []

    wti = get(series, "wti")
    brent = get(series, "brent")
    if wti:
        r = pct_rank(wti)
        tr = trend_word(wti)
        t = f"WTI is at ${wti['latest']:.2f}/bbl"
        if brent:
            t += f" and Brent at ${brent['latest']:.2f}/bbl"
        if r is not None:
            t += f", which sits higher than {r}% of readings over the past five years"
        if tr:
            t += f", and the trend across recent observations is {tr}"
        t += ". Energy is the single largest swing factor behind headline inflation volatility, which means it feeds directly into the Fed's reaction function and therefore into your base-rate assumptions."
        t += " On the portfolio side, sustained moves here reset input costs for transport-, logistics-, chemicals- and manufacturing-exposed borrowers, and are worth stress-testing in any credit model that assumed energy costs stay near recent levels."
        items.append({"tag": "Energy", "text": t})

    pay = get(series, "payrolls")
    un = get(series, "unemployment")
    claims = get(series, "claims")
    ahe = get(series, "ahe")
    if pay or un:
        t = ""
        if pay:
            vals = pay.get("values", [])
            avg3 = sum(vals[-3:]) / min(3, len(vals)) if vals else None
            t += f"Payrolls added {pay['latest']:+.0f}K in the latest report"
            if avg3 is not None:
                t += f", against a {avg3:+.0f}K average across the last three readings"
        if un:
            t += (f", and unemployment stands at {un['latest']:.1f}%" if t else f"Unemployment stands at {un['latest']:.1f}%")
        if claims:
            t += f". Weekly initial claims are running near {claims['latest']/1000:.0f}K"
        if ahe:
            t += f", with average hourly earnings up {ahe['latest']:.1f}% year over year"
        t += ". Hiring momentum is the most reliable leading read on demand durability for growth-oriented credits: payroll deceleration typically shows up in borrower topline growth and covenant headroom a quarter or two before it appears in the credit metrics themselves."
        t += " Wage growth running above pre-pandemic norms also keeps services inflation sticky, which matters for labor-intensive issuers where payroll is the dominant variable cost."
        items.append({"tag": "Labor", "text": t})

    cpi = get(series, "cpiHeadline")
    core = get(series, "cpiCore")
    pce = get(series, "pceCore")
    ppi = get(series, "ppi")
    if cpi or core or pce:
        t = ""
        if cpi:
            d = chg(cpi)
            t += f"Headline CPI is running {cpi['latest']:.1f}% year over year"
            if d is not None:
                t += f" ({d:+.1f}pt versus the prior reading)"
        if core:
            t += (f", with core CPI at {core['latest']:.1f}%" if t else f"Core CPI is {core['latest']:.1f}%")
        if pce:
            t += f" and core PCE — the metric the FOMC actually targets — at {pce['latest']:.1f}%"
        if ppi:
            t += f". Producer prices are up {ppi['latest']:.1f}% year over year, the earliest read on what is coming at borrower input costs"
        t += ". The central tension is the gap between volatile headline prints, which are largely energy-driven and mean-revert, and the stickier core measures that determine the policy path."
        t += " For underwriting, the practical implication is to price to sticky rather than falling financing costs until core decelerates convincingly, and to watch pricing power closely at issuers absorbing input-cost inflation they cannot pass through."
        items.append({"tag": "Inflation", "text": t})

    ff = get(series, "fedfunds")
    curve = get(series, "curve")
    t10 = get(series, "ust10y")
    t2 = get(series, "ust2y")
    if ff or t10:
        t = ""
        if ff:
            t += f"The Fed funds target stands at {ff['latest'] - 0.25:.2f}–{ff['latest']:.2f}%"
        if t10:
            t += (f", the 10-year Treasury at {t10['latest']:.2f}%" if t else f"The 10-year Treasury is at {t10['latest']:.2f}%")
        if t2:
            t += f" and the 2-year at {t2['latest']:.2f}%"
        if curve:
            bp = round(curve["latest"] * 100)
            shape = "its normal upward slope" if bp > 0 else "inversion"
            t += f". The 10Y-minus-2Y curve sits at {bp:+d}bp, holding {shape}"
            t += (", which is generally constructive for credit: it is not pricing imminent recession and it widens the margin banks and direct lenders earn borrowing short and lending long."
                  if bp > 0 else
                  ", historically one of the more reliable recession warnings and a signal the market expects cuts in response to weaker growth.")
        t += " The front end is your fastest read on repricing of hike-versus-cut odds around each CPI, PCE and jobs print, while the long end drives enterprise valuations and refinancing math."
        t += " Every floating-rate structure in the book keys off the short end, so a persistent move here changes the cost-of-capital assumption underneath new originations mid-process."
        items.append({"tag": "Policy & Rates", "text": t})

    hy = get(series, "hyoas")
    ig = get(series, "igoas")
    if hy or ig:
        t = ""
        if hy:
            r = pct_rank(hy)
            bp = round(hy["latest"] * 100)
            t += f"High-yield OAS is {bp}bp"
            if r is not None:
                t += (f", tighter than {100 - r}% of the past five years" if r < 50
                      else f", wider than {r}% of the past five years")
            tr = trend_word(hy)
            if tr:
                t += f", and {tr} across recent observations"
        if ig:
            t += (f". Investment-grade OAS is {round(ig['latest'] * 100)}bp" if t
                  else f"Investment-grade OAS is {round(ig['latest'] * 100)}bp")
        t += ". Index spreads are the cleanest market-priced gauge of how much you are actually being paid for credit risk."
        t += " The pattern worth watching for special situations sourcing is compression at the index level masking widening dispersion underneath: idiosyncratic stress tends to surface in specific issuers — leveraged, tariff-exposed, or facing near-term maturities — well before the benchmark moves. That dispersion, not the index level, is where the opportunity set originates."
        items.append({"tag": "Credit", "text": t})

    gdp = get(series, "gdp")
    gnow = get(series, "gdpnow")
    ism = get(series, "ismMfg")
    if gdp or gnow or ism:
        t = ""
        if gdp:
            t += f"Real GDP grew {gdp['latest']:.1f}% (QoQ SAAR) in the most recent quarter"
        if gnow:
            t += ((" and the Atlanta Fed's GDPNow is tracking " if t else "The Atlanta Fed's GDPNow is tracking ")
                  + f"{gnow['latest']:+.1f}% for the current quarter, an early read ahead of the official advance estimate")
        if ism:
            state = "expansion" if ism['latest'] >= 50 else "contraction"
            t += f". ISM Manufacturing is at {ism['latest']:.1f}, indicating {state}"
        t += ". Below-trend but positive growth is the backdrop that supports growth-oriented positioning over distressed or turnaround exposure."
        t += " A material downside surprise would shift the opportunity set the other way, toward stressed and special situations, so the gap between the nowcast and the official print is worth tracking as an early warning rather than waiting on the lagging release."
        items.append({"tag": "Growth", "text": t})

    um = get(series, "umich")
    hs = get(series, "housingStarts")
    mort = get(series, "mortgage30y")
    sav = get(series, "saving")
    if um or hs or mort:
        t = ""
        if um:
            r = pct_rank(um)
            t += f"University of Michigan consumer sentiment is {um['latest']:.1f}"
            if r is not None:
                t += f", lower than {100 - r}% of readings over the past five years"
        if sav:
            t += (f", and the personal saving rate is {sav['latest']:.1f}%" if t
                  else f"The personal saving rate is {sav['latest']:.1f}%")
        if hs:
            t += (f". Housing starts are running {hs['latest']/1000:.2f}M SAAR" if t
                  else f"Housing starts are running {hs['latest']/1000:.2f}M SAAR")
        if mort:
            t += f" against a {mort['latest']:.2f}% 30-year mortgage rate"
        t += ". Housing and the consumer are the most rate-sensitive parts of the demand picture and typically the first to register policy tightening."
        t += " A low saving rate means spending is running ahead of income growth, which is durable while labor income holds but drains quickly if payroll growth decelerates — worth tracking alongside the jobs data for consumer-facing and housing-cycle-exposed borrowers."
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
7. Some official series are published to FRED on a delay. Find the MOST RECENT published reading (preliminary is fine) for each of these, with the month it refers to and whether it is prelim or final. Only include one if you are confident in the number from a reputable source (a news report of the official release is fine); omit any you cannot verify. Values:
   - "umich" = University of Michigan Consumer Sentiment headline index (roughly 40-110)
   - "existingHome" = US existing home sales, millions SAAR (roughly 3.0-6.5)
   - "pceHeadline" = headline PCE inflation, YoY % (e.g. 3.4)
   - "pceCore" = core PCE inflation, YoY % (e.g. 3.1)
   - "saving" = US personal saving rate, % (e.g. 4.5)
   - "retailYoY" = US retail & food services sales EX motor vehicles, YoY % (e.g. 4.2). Report year-over-year percent, not month-over-month.
   - "ismMfg" = ISM Manufacturing PMI headline (roughly 40-65). The official ISM number is not on FRED, so this news-sourced value is the only way to keep it current.

Respond with ONLY this JSON (no markdown fences, no other text):
{{"takeaways":[{{"tag":"Energy","text":"..."}}],
"extraSeries":{{"ecb":{{"value":"N.NN%","delta":"context","dir":"up|down|flat"}},"chinaPMI":{{"value":"NN.N","delta":"context","dir":"up|down|flat"}},"ismServices":{{"value":"NN.N","delta":"context","dir":"up|down|flat"}},"retailSales":{{"value":"+N.N% m/m","delta":"context","dir":"up|down|flat"}},"existingHome":{{"value":"N.NNM","delta":"context","dir":"up|down|flat"}}}},
"themeCards":[{{"title":"3-6 word title","value":"Elevated","dir":"up|down|flat","statusLine":"1-2 sentence current situation","relevance":"1-2 sentences on why it matters for special situations credit"}}],
"consensus":{{"CPI":"3.4% YoY exp","Payrolls":"+80K exp"}},
"surprises":[{{"label":"CPI","actual":"3.5%","consensus":"3.4%","dir":"up"}}],
"regime":{{"label":"Late-cycle","detail":"tight spreads, cooling labor, sticky core inflation"}},
"freshPoints":{{"umich":{{"value":51.2,"month":"2026-07","kind":"prelim"}},"pceHeadline":{{"value":4.1,"month":"2026-06","kind":"final"}},"pceCore":{{"value":3.4,"month":"2026-06","kind":"final"}},"saving":{{"value":4.2,"month":"2026-06","kind":"final"}},"retailYoY":{{"value":4.5,"month":"2026-06","kind":"final"}},"ismMfg":{{"value":49.0,"month":"2026-07","kind":"final"}}}}}}"""


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
        text = strip_citations(text)
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError("no JSON in response")
        parsed = json.loads(m.group(0))
        if not parsed.get("takeaways"):
            raise ValueError("no takeaways in response")
        return scrub(parsed)
    except Exception as e:
        print(f"Claude enhancement failed ({e}); using rule-based commentary.", file=sys.stderr)
        return None


# ----------------------------------------------------------------------

# Series that FRED publishes on a delay, where a news-sourced latest point is
# worth appending. "scale" converts the reported unit into the FRED unit;
# "lo"/"hi" bound a sane value for validation (reject anything outside).
FRESH_POINT_SPEC = {
    "umich":        {"scale": 1,         "lo": 30,   "hi": 120},   # sentiment index
    "existingHome": {"scale": 1_000_000, "lo": 2.5,  "hi": 7.5},   # millions SAAR -> units
    "pceHeadline":  {"scale": 1,         "lo": -2.0, "hi": 12.0},  # YoY %
    "pceCore":      {"scale": 1,         "lo": -2.0, "hi": 12.0},  # YoY %
    "saving":       {"scale": 1,         "lo": 0.0,  "hi": 20.0},  # %
    "retailYoY":    {"scale": 1,         "lo": -20.0, "hi": 25.0}, # retail sales YoY %
    "ismMfg":       {"scale": 1,         "lo": 35.0, "hi": 70.0},  # ISM Mfg PMI (FRED copy frozen)
}


def _norm_month(s):
    """Normalize '2026-07' or '2026-07-15' to a first-of-month ISO date."""
    s = str(s).strip()
    if len(s) == 7:
        return s + "-01"
    if len(s) == 10:
        return s[:7] + "-01"
    return None


def apply_fresh_points(series, fresh):
    """Append a validated, news-sourced latest point to delayed FRED series.

    Runs from clean FRED data each time (fetch_data rewrites series before
    this), so points never compound. The appended point is flagged
    provisional so the frontend can mark it distinctly.
    """
    if not isinstance(fresh, dict):
        return 0
    applied = 0
    for key, spec in FRESH_POINT_SPEC.items():
        fp = fresh.get(key)
        s = series.get(key)
        if not fp or not s or "values" not in s or "dates" not in s:
            continue
        try:
            raw = float(fp.get("value"))
        except (TypeError, ValueError):
            continue
        if not (spec["lo"] <= raw <= spec["hi"]):
            print(f"  fresh point for {key} rejected: {raw} out of range", file=sys.stderr)
            continue
        month = _norm_month(fp.get("month") or fp.get("date") or "")
        if not month:
            continue
        # Only append if strictly newer than FRED's latest observation.
        if s["dates"] and month <= s["dates"][-1]:
            continue
        value = round(raw * spec["scale"], 4)
        s["dates"].append(month)
        s["values"].append(value)
        s["prev"] = s["latest"]
        s["latest"] = value
        s["latestDate"] = month
        s["provisionalLast"] = True
        s["provisionalKind"] = fp.get("kind", "prelim")
        # Refresh trailing-range stats to include the new point.
        vals = s["values"]
        s["min5y"], s["max5y"] = round(min(vals), 4), round(max(vals), 4)
        applied += 1
        print(f"  appended provisional {key}: {value} ({month}, {s['provisionalKind']})")
    return applied


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
        applied = apply_fresh_points(series, enhanced.get("freshPoints"))
        print(f"Commentary: Claude-enhanced ({len(data.get('themeCards', []))} theme cards, "
              f"{len(data.get('consensus', {}))} consensus, {len(data.get('surprises', []))} surprises, "
              f"{applied} fresh points appended).")
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

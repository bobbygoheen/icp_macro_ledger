# The ICP Macro Ledger — self-hosted website

A macro dashboard for special situations credit investing that lives on a URL
you control, refreshes itself automatically, and costs $0/month to run.

## How it works — no Anthropic, no AI, no servers

- **`index.html`** — the dashboard. Pure static page (React via CDN).
- **`data.json`** — the live data. Latest values + ~5 years of real history
  for ~24 series (oil, Treasury yields, credit spreads, CPI/PCE/PPI, payrolls,
  unemployment, claims, UMich, saving rate, mortgage rates, housing starts,
  GDP, GDPNow, VIX, Fed funds).
- **`scripts/fetch_data.py`** — pulls all of it from the **St. Louis Fed's
  FRED API** (the official, free source for exactly this data).
- **`.github/workflows/update-data.yml`** — a scheduled GitHub Action that
  runs the script twice per weekday and commits the fresh `data.json`.
- **GitHub Pages** serves the whole thing at a public URL, with custom-domain
  support if you want `macro.yourdomain.com`.

A seed `data.json` snapshot (reviewed July 16, 2026) is included so the site
renders sensibly even before your first refresh run.

## Setup (~15 minutes, no coding)

### 1. Get a free FRED API key
Go to https://fred.stlouisfed.org/docs/api/api_key.html → create a free
account → request an API key. It's instant and free with generous limits.

### 2. Create a GitHub repo with these files
- Create a new **public** repo on github.com (public is required for free
  GitHub Pages; the data here is all public anyway).
- Upload everything in this folder, **preserving the folder structure**
  (`.github/workflows/update-data.yml` and `scripts/fetch_data.py` must keep
  their paths). Easiest way: on the repo page, "Add file → Upload files" and
  drag the whole folder contents in.

### 3. Add your FRED key as a repo secret
Repo → Settings → Secrets and variables → Actions → New repository secret:
- Name: `FRED_API_KEY`
- Value: your key from step 1

### 4. Turn on GitHub Pages
Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
folder: `/ (root)` → Save. After a minute or two your site is live at
`https://<your-username>.github.io/<repo-name>/`

### 5. Run the first data refresh
Repo → Actions tab → "Refresh macro data" → "Run workflow". This fetches
fresh data from FRED and commits `data.json`. From then on it runs
automatically twice per weekday (you can edit the cron schedule in
`.github/workflows/update-data.yml`).

### 6. (Optional) Custom domain
Settings → Pages → Custom domain → enter e.g. `macro.yourdomain.com`, then
add the CNAME record at your DNS provider per GitHub's instructions.

## The commentary layer (new)

"This Week's Read" is now generated on every refresh, not hand-written:

- **Default (free, no extra setup):** a rules engine computes takeaways
  directly from the fresh FRED data — current levels, changes, 3-month
  trends, and 5-year percentile context (e.g. "HY OAS is tighter than 92% of
  the past five years").
- **Enhanced (optional):** add a second repo secret `ANTHROPIC_API_KEY`
  (from https://console.anthropic.com) and each refresh instead asks Claude —
  with web search — to write richer takeaways grounded in the data *plus*
  current news, fetch the values FRED doesn't carry (ECB rate, China PMI,
  ISM Services, retail sales, existing home sales), and update the three
  qualitative theme cards (refi wall, tariffs, Middle East). Costs roughly a
  cent or two per refresh against your Anthropic account. If the call fails
  for any reason, the rules engine output is used instead — the site never
  breaks.

Next-release dates are computed automatically: exactly for deterministic
schedules (FOMC and ECB calendars, jobs = first Friday, ISM = 1st/3rd
business day, claims = Thursdays) and approximately for the rest (marked
"est." on the card).

## What refreshes vs. what doesn't

**Auto-refreshing (LIVE tag, real FRED history, exact chart dates):** oil,
Treasury yields, yield curve, HY/IG spreads, Fed funds, CPI, core CPI, PCE,
core PCE, PPI, payrolls, unemployment, wages, claims, UMich, saving rate,
mortgage rate, housing starts, GDP, GDPNow, VIX.

**Refreshing via commentary layer:** "This Week's Read" takeaways (every
refresh), next-release dates (every refresh), the Credit Gauge percentile
line (computed from live history), and — with the enhanced option — ECB
rate, China PMI, ISM Services, retail sales, existing home sales, and the
three theme cards.

**Still static (by design):** the per-card "why it matters" notes. These are
structural explanations of why each indicator matters for special situations
credit — they describe relationships, not current readings, so they only
change when you ask for a revision. Without the enhanced option, the
non-FRED values and theme cards also remain a reviewed snapshot (last
reviewed July 16, 2026).

*The script attempts ISM (NAPM) and existing home sales (EXHOSLUSM495S)
series, but FRED has restricted/removed these at times; if unavailable
they're skipped gracefully and the reviewed snapshot shows instead.

## Refresh frequency

The default schedule is 10:30am and 5:30pm ET on weekdays. Note that FRED
daily series (yields, spreads, oil) post with a 1-day lag, so "latest" for
those is typically the prior business day's close — standard for free data.
Want intraday quotes? That requires a paid market-data feed; happy to wire
one in if you ever want it.

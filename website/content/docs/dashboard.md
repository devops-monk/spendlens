# Dashboard & output formats

## Every command, every format

```bash
spendlens waste                          # table (default)
spendlens waste -f csv -o waste.csv
spendlens waste -f json -o waste.json    # {meta, count, rows}
spendlens waste -f html -o waste.html    # an interactive findings report (below)
```

## The findings report: `-f html` on tags, rightsize and waste

The check commands write a full report rather than a plain table. It contains:
- **Summary:** failing resources, compliance %, estimated saving per month and per year, and how many checks fail, with a status bar.
- **Largest savings:** the five biggest opportunities.
- **Checks overview:** one line per check, worst first. Each line shows a fail bar, "14,733 of 14,852 fail", the thresholds the check used and its saving. Click a line to jump to that check.
- **Toolbar:** search, status filters (Alarm, Info, Skip, Error, OK), and service and region filters. It stays at the top as you scroll.
- **One collapsible section per check:**
  - **Checks:** what the check looks for.
  - **Threshold:** the exact settings from `finops.yaml` this run used, e.g. `sizing.cloudwatch_log_group_retention_max_days = 1`. This tells you both the limit and where to change it.
  - **Results:** the counts per status.
  - Every resource follows, with its status, readable name and full ARN, the finding (why it passed or failed, with the current value), account, region and saving. Failures are listed first.
- **Fast at any size:** each check shows its first 50 results, then **Show more** and **Show all**. Search and filters work across every result, including ones not yet shown. Tens of thousands of results stay responsive, and the file stays small.
- **Download CSV:** exports exactly the rows you've filtered to, with the same columns as `-f csv`.
- **Print:** expands every check.

Like the dashboard, it's one self-contained file that works offline and as a CI artifact, and it follows your branding. For example, to get the old `steampipe check` scope as one report:

```bash
spendlens rightsize -S rds -S ecs -S cloudwatch --status all -f html -o checks.html
```

`--summary` output stays a compact sortable table.

## spendlens dashboard

```bash
spendlens dashboard -o report.html                 # reuses today's runs, runs the rest live
spendlens dashboard --refresh -o report.html       # re-run every section now
spendlens dashboard --source last -o report.html   # saved runs only, no AWS calls
spendlens dashboard --source demo -o demo.html     # sample data
```

**Every section is covered, and at most a day old:**
- A section already run **today** (UTC) is read from your history: no AWS calls for it.
- Every other section is **run live first**: read-only AWS calls, plus Cost Explorer (about $0.01 per request) for the cost sections. Each run is saved, so the next run today reuses it.
- `--refresh` re-runs everything now.
- `--source last` never calls AWS; sections with no saved run say which command fills them.

If a section can't run (e.g. no Cost Explorer permission), the output says why and uses its last saved run.

[[shot:hero|The Overview tab of the dashboard, from the bundled demo data.]]

One self-contained HTML file. It has no network requests, so it works offline, as a CI artifact, and printed. It has nine tabs:
- **Overview:** the headline spend and its change, plus tiles for flagged changes, projected month end, potential saving, tag compliance and anomalies.
- **Cost diff & trend**
- **Showback**
- **Anomalies**
- **Forecast**
- **Tags**
- **Rightsizing**
- **Waste**
- **Savings**

**Every section explains itself.** A **How to read this** box on each tab says:
- **What it shows.**
- **How it's calculated:** with your actual thresholds and the `finops.yaml` setting that changes each one, e.g. "flagged = at least $10 and at least 20% (cost.change_alarm_abs / cost.change_alarm_pct)".
- **What to do next.**

The same charts and explanations appear when you run a single cost command with `-f html` (`cost diff`, `cost trend`, `cost showback`, `anomalies`, `forecast`).

How the charts behave:
- Every chart has hover and keyboard tooltips, and a **Table view**.
- Chart colours are a palette validated for colour-blind readers in both light and dark mode.
- The page embeds its data as JSON (`<script type="application/json" id="spendlens-data">`), so other tools can read the numbers.

`spendlens report` runs every check and then builds the dashboard from those runs (`--source last`), writing each check's JSON as well.

## spendlens brief: one page for managers

```bash
spendlens brief -o brief.html                        # AI summary; reuses today's runs, runs the rest live
spendlens brief --source last -o brief.html          # saved runs only, no AWS calls
spendlens brief --source last --no-ai -o brief.html  # nothing sent to an AI provider
spendlens brief --source demo -o brief.html          # sample data
```

The dashboard has every detail; the brief is the page you send to a manager. One printable, self-contained HTML file:
- **Analyst summary:** a headline and a short summary of what happened to spend, why, and what needs attention, written by the FinOps AI profile (your `llm.*` provider).
- **Five numbers:** last month's spend and its change, this month's forecast against the budget, savings to review, anomalies and tag compliance, each coloured good, warning or bad.
- **Key insights:** up to six, ranked by impact and labelled cost, forecast, anomaly, savings or governance.
- **Charts:** monthly spend as columns with this month's forecast and a budget line, what changed (increases and decreases), the trend of the top services, where the money goes and spend by owner tag.
- **Anomalies:** each spike with its usual level and extra spend, plus the likely cause when the AI wrote the summary.
- **Savings for review:** the total by source and by service, recommended next steps, and a **review checklist** of the 12 largest proposals. Tick an item once someone has reviewed it; the tick is saved in that browser.
- **Budget and governance:** forecast and tag-compliance meters, and risks to watch.
- **About this report:** which saved run each section used, and when.

[[shot:brief|The top of the executive brief, from the bundled demo data (rule-based summary).]]

**Every number is computed by spendlens.** The AI receives those figures, may run a few read-only queries on your saved history (never live AWS), and writes only the words. A saving it states that is larger than every saving found put together is dropped. If the AI isn't configured, fails, or doesn't return the expected answer, the brief is still written with the built-in rule-based summary and says so. `--no-ai` always uses the rule-based summary.

`spendlens report` also writes `brief.html` (rule-based; add `--ai` for the AI summary).

## Branding

```yaml
dashboard:
  title: "Platform FinOps report"
  organisation: "Example Group"
  brand_primary: "#0071e3"       # accent: brand mark, active tab, hero stripe, buttons
  brand_ink: "#000000"           # organisation chip background
  brand_on_ink: "#ffffff"        # organisation chip text
  font_family: '"Brand Sans", system-ui, sans-serif'
```

Brand colours style the page chrome only. Chart data colours stay on the accessible palette.
- **`brand_primary`:** the accent. It colours the brand mark, the active-tab indicator, the hero stripe, buttons and focus rings.
- **`brand_ink` and `brand_on_ink`:** colour the organisation chip next to the title.
- **Font:** the default is the system font: SF Pro on Apple devices, Segoe UI on Windows, Roboto on Android.

## Customising the layout

Layout and style live in package files: `templates/report/*.html.j2`, `style.css` and `app.js`. Python only builds the data; the templates present it. Plugins can add their own tab (see [Extending](../extending/)).

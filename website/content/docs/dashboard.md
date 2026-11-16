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
- **Summary:** alarms, compliance %, estimated saving per month and per year, and the number of checks, with a status bar.
- **Largest savings:** the five biggest opportunities.
- **Toolbar:** search, status filters (Alarm, Info, Skip, Error, OK) and a service filter.
- **One collapsible section per check:** pass and fail counts, the saving, and every resource with its reason, account and region. Checks with alarms open first.
- **Download CSV:** exports exactly the rows you've filtered to, with the same columns as `-f csv`.
- **Print:** expands every check.

Like the dashboard, it's one self-contained file that works offline and as a CI artifact, and it follows your branding. For example, to get the old `steampipe check` scope as one report:

```bash
spendlens rightsize -S rds -S ecs -S cloudwatch --status all -f html -o checks.html
```

`--summary` output stays a compact sortable table.

## spendlens dashboard

```bash
spendlens dashboard -o report.html               # from your latest saved runs
spendlens dashboard --source demo -o demo.html   # sample data
```

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

How the charts behave:
- Every chart has hover and keyboard tooltips, and a **Table view**.
- Chart colours are a palette validated for colour-blind readers in both light and dark mode.
- The page embeds its data as JSON (`<script type="application/json" id="spendlens-data">`), so other tools can read the numbers.

The dashboard makes no AWS calls; it reads saved runs. Run the checks first, or use `spendlens report`, which does both.

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

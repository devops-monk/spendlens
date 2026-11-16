# Dashboard & output formats

## Every command, every format

```bash
spendlens waste                          # table (default)
spendlens waste -f csv -o waste.csv
spendlens waste -f json -o waste.json    # {meta, count, rows}
spendlens waste -f html -o waste.html    # branded, sortable, self-contained page
```

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
  brand_primary: "#0071e3"       # accent: header rule, active tab
  brand_ink: "#000000"           # header background
  brand_on_ink: "#ffffff"
  font_family: '"Brand Sans", system-ui, sans-serif'
```

Brand colours style the page chrome only. Chart data colours stay on the accessible palette.

## Customising the layout

Layout and style live in package files: `templates/report/*.html.j2`, `style.css` and `app.js`. Python only builds the data; the templates present it. Plugins can add their own tab (see [Extending](../extending/)).

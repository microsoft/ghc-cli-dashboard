---
layout: default
title: Development
---

# Development

Install dependencies and run the test suite:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -v
```

The GitHub Actions workflow runs the test suite on Python 3.9 with Node.js.
Node executes the lightweight DOM harness used for generated-dashboard tests.

Keep generated CSV and HTML files out of commits. They may contain personal
usage data, project names, and task summaries.

## Documentation screenshots

The screenshots in the README are built from synthetic data, so no real usage
data ever enters the repository. Regenerate them after changing the dashboard
layout:

```powershell
python -m pip install playwright
playwright install chromium

python tools/make_sample_data.py --out sample_usage.csv
python dashboard.py --in sample_usage.csv --out sample_dashboard.html
python tools/make_screenshots.py --html sample_dashboard.html --out-dir docs/images
```

`tools/make_sample_data.py` uses a fixed random seed and a fixed date range,
so repeated runs produce identical output. Both `sample_usage.csv` and
`sample_dashboard.html` are gitignored.

`dashboard.py` obtains the bundled Plotly.js source from
`plotly.offline.get_plotlyjs()`. When editing chart titles, use Plotly's
object form:

```javascript
title: { text: "Chart title" }
```

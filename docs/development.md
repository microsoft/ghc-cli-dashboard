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

`dashboard.py` obtains the bundled Plotly.js source from
`plotly.offline.get_plotlyjs()`. When editing chart titles, use Plotly's
object form:

```javascript
title: { text: "Chart title" }
```

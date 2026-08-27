# Copilot CLI Usage Dashboard

[![Test](https://github.com/microsoft/ghc-cli-dashboard/actions/workflows/test.yml/badge.svg)](https://github.com/microsoft/ghc-cli-dashboard/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](docs/compatibility.md)

An experimental local dashboard for exploring GitHub Copilot CLI usage. It
reads the CLI session store, exports aggregate usage data to CSV, and creates
a self-contained HTML dashboard.

The local session-store schema is not a stable public API. Copilot CLI updates
can change the extractor's assumptions. See
[Compatibility and data format](docs/compatibility.md).

## Quick start

Requirements:

- Python 3.9 or later
- GitHub Copilot CLI session data on the local machine

```powershell
git clone https://github.com/microsoft/ghc-cli-dashboard.git
cd ghc-cli-dashboard
python -m pip install -r requirements.txt

python extract_usage.py
python dashboard.py --in "copilot_usage_*.csv" --out usage_dashboard.html
```

Open `usage_dashboard.html` in a browser.

Task summaries are excluded by default. Add `--include-task-summary` only if
you need the Work patterns view and have reviewed the privacy implications.

## What it shows

- Token usage and estimated list-price cost by project, model, and provider.
- Trends by day, week, or month.
- Input, output, cache, and reasoning token categories.
- Model mix, provider mix, reasoning effort, and task detail.

The dashboard estimates cost from usage data recorded by Copilot CLI. It does
not represent an invoice.

## Privacy

The export CSV and generated dashboard can contain usernames, project names,
session IDs, dates, models, and optional task summaries. Generated HTML
embeds its source rows.

Review files before sharing them. Use build-time redaction when needed:

```powershell
python dashboard.py --in "copilot_usage_*.csv" --out shared_dashboard.html `
  --exclude-project "Personal Project" `
  --omit-task-summaries
```

The `.gitignore` excludes generated CSV and HTML files. Do not commit them.
See [Advanced usage](docs/advanced-usage.md) for sharing, redaction, and
combining multiple exports.

## Documentation

| Topic | Guide |
| --- | --- |
| Compatibility, CSV format, and schema boundary | [Compatibility and data format](docs/compatibility.md) |
| Token fields and cost estimates | [Token and cost data](docs/token-and-costs.md) |
| Privacy, redaction, filters, providers, and combined exports | [Advanced usage](docs/advanced-usage.md) |
| Development and tests | [Development](docs/development.md) |

## Dependencies

The project uses [pandas](https://pandas.pydata.org/) for data processing and
[Plotly](https://plotly.com/python/) for charts. Generated dashboards bundle
Plotly.js so they can open without a web server.

## Contributing and support

- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security reporting](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [MIT License](LICENSE.txt)

## Telemetry

This project does not collect, transmit, or enable telemetry. It reads local
files and writes local CSV and HTML files.

## Trademarks

This project may contain trademarks or logos for projects, products, or
services. Authorized use of Microsoft trademarks or logos is subject to and
must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general.aspx).
Use of Microsoft trademarks or logos in modified versions of this project must
not cause confusion or imply Microsoft sponsorship. Any use of third-party
trademarks or logos is subject to those third-party's policies.

# Contributing

This project welcomes contributions and suggestions.

Most contributions require you to agree to a Contributor License Agreement
(CLA) declaring that you have the right to, and actually do, grant us the
rights to use your contribution. For details, visit
[Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine
whether you need to provide a CLA and decorate the PR appropriately. Follow
the instructions provided by the bot. You need to complete this only once
across all repositories using our CLA.

## Before opening an issue or pull request

- Search existing issues and pull requests to avoid duplicate work.
- Do not include Copilot usage exports, generated dashboards, project names,
  task summaries, session IDs, credentials, or other personal/confidential
  data in issues, pull requests, screenshots, or commits.
- For security vulnerabilities, follow [SECURITY.md](SECURITY.md) rather
  than opening a public issue.

## Development

Install the project and test dependencies, then run the test suite:

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -v
```

Keep generated CSV and HTML files out of commits. The repository's
`.gitignore` excludes them because they can contain personal usage data and
project or task information.

## Pull requests

- Keep changes focused and include tests for changed behavior.
- Use descriptive commit messages and explain user-visible changes in the
  pull request.
- Update the README when behavior, prerequisites, privacy guidance, or
  command-line usage changes.

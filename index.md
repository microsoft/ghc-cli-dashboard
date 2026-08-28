---
layout: default
title: Home
nav_order: 1
---

{%- comment -%}
The landing page reuses README.md so the two never drift apart.

jekyll-relative-links only rewrites links in a page's own source, not in
content pulled in by include_relative, so the README's relative "docs/*.md"
links would otherwise 404 here. Rewriting the extensions after the capture
fixes them, and leaves README.md holding plain relative links that still work
in the GitHub file view.
{%- endcomment -%}

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

{% capture readme %}{% include_relative README.md %}{% endcapture %}
{{ readme
   | replace: 'docs/compatibility.md', 'docs/compatibility.html'
   | replace: 'docs/token-and-costs.md', 'docs/token-and-costs.html'
   | replace: 'docs/advanced-usage.md', 'docs/advanced-usage.html'
   | replace: 'docs/development.md', 'docs/development.html'
   | replace: '](CONTRIBUTING.md)', '](https://github.com/microsoft/ghc-cli-dashboard/blob/main/CONTRIBUTING.md)'
   | replace: '](SUPPORT.md)', '](https://github.com/microsoft/ghc-cli-dashboard/blob/main/SUPPORT.md)'
   | replace: '](SECURITY.md)', '](https://github.com/microsoft/ghc-cli-dashboard/blob/main/SECURITY.md)'
   | replace: '](CODE_OF_CONDUCT.md)', '](https://github.com/microsoft/ghc-cli-dashboard/blob/main/CODE_OF_CONDUCT.md)'
   | replace: '](LICENSE.txt)', '](https://github.com/microsoft/ghc-cli-dashboard/blob/main/LICENSE.txt)' }}


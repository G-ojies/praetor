#!/usr/bin/env python3
"""Build the GitHub Pages site from the blog post.

Self-contained HTML with a `.nojekyll` marker rather than a Jekyll theme: the
page then has no build step to break, and it can carry the same palette as the
console and the submission thumbnail instead of looking like a default template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "blog-post.md"
OUT = ROOT / "docs" / "index.html"

TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The agent may move the lab toward safety, and never away from it</title>
<meta name="description" content="Building an autonomous agent fleet you can actually point at a working clinical laboratory.">
<meta property="og:title" content="The agent may move the lab toward safety, and never away from it">
<meta property="og:description" content="Building an autonomous agent fleet you can actually point at a working clinical laboratory.">
<meta property="og:type" content="article">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,400;0,500;1,400&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --ground:#F6F8F9;--surface:#FFF;--surface-2:#EDF1F3;--ink:#10161C;--ink-2:#47555F;
  --ink-3:#7A8791;--rule:#D8E0E4;--rule-strong:#BCC8CE;--accent:#0E7C86;--accent-soft:#DFEFF0;
  --allow:#2E7A5B;--allow-bg:#E2F0E9;--deny:#A5382F;--deny-bg:#F6E2E0;--esc:#A8720F;--esc-bg:#F6EBD8;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --ground:#0D1215;--surface:#141B20;--surface-2:#1C252B;--ink:#E6ECEF;--ink-2:#9BA9B2;
  --ink-3:#6E7C85;--rule:#263239;--rule-strong:#35454E;--accent:#3FB3BD;--accent-soft:#123033;
  --allow:#5CAE87;--allow-bg:#13291F;--deny:#D2685C;--deny-bg:#2C1917;--esc:#D69B3C;--esc-bg:#2C2314;
}}}}
:root[data-theme="dark"]{{
  --ground:#0D1215;--surface:#141B20;--surface-2:#1C252B;--ink:#E6ECEF;--ink-2:#9BA9B2;
  --ink-3:#6E7C85;--rule:#263239;--rule-strong:#35454E;--accent:#3FB3BD;--accent-soft:#123033;
  --allow:#5CAE87;--allow-bg:#13291F;--deny:#D2685C;--deny-bg:#2C1917;--esc:#D69B3C;--esc-bg:#2C2314;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
  font-family:"Public Sans",system-ui,sans-serif;font-size:17px;line-height:1.68;
  -webkit-font-smoothing:antialiased}}
.wrap{{max-width:720px;margin:0 auto;padding:0 24px 110px}}
header{{padding:78px 0 0}}
.eyebrow{{font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--accent);margin-bottom:24px}}
h1{{font-family:Newsreader,Georgia,serif;font-weight:500;font-size:clamp(36px,5.6vw,52px);
  line-height:1.1;letter-spacing:-.018em;margin:0 0 18px;text-wrap:balance}}
.standfirst{{font-family:Newsreader,Georgia,serif;font-style:italic;font-size:21px;
  color:var(--ink-2);margin:0 0 14px}}
.byline{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-3);
  border-top:1px solid var(--rule);padding-top:20px;margin-top:34px}}
.byline a{{color:var(--accent)}}
article{{margin-top:46px}}
h2{{font-family:Newsreader,Georgia,serif;font-weight:500;font-size:31px;letter-spacing:-.012em;
  margin:56px 0 14px;text-wrap:balance}}
h3{{font-size:17px;font-weight:600;margin:34px 0 8px}}
p{{margin:0 0 20px;text-wrap:pretty}}
blockquote{{margin:30px 0;padding:16px 22px;border-left:3px solid var(--accent);
  background:var(--accent-soft);border-radius:0 4px 4px 0}}
blockquote p{{margin:0;font-family:Newsreader,Georgia,serif;font-size:20px;line-height:1.5}}
code{{font-family:"IBM Plex Mono",monospace;font-size:.85em;background:var(--surface-2);
  padding:2px 6px;border-radius:3px}}
pre{{background:var(--surface-2);border:1px solid var(--rule);border-radius:4px;
  padding:16px 18px;overflow-x:auto;margin:0 0 22px}}
pre code{{background:none;padding:0;font-size:13.5px;line-height:1.65}}
table{{border-collapse:collapse;width:100%;font-size:15px;margin:0 0 24px;display:block;
  overflow-x:auto}}
th{{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3);text-align:left;font-weight:500;
  padding:10px 14px;border-bottom:1px solid var(--rule-strong)}}
td{{padding:11px 14px;border-bottom:1px solid var(--rule);vertical-align:top}}
strong{{font-weight:600}}
a{{color:var(--accent);text-underline-offset:3px}}
a:focus-visible{{outline:2px solid var(--accent);outline-offset:3px;border-radius:2px}}
hr{{border:none;border-top:1px solid var(--rule);margin:52px 0}}
ul,ol{{margin:0 0 22px;padding-left:24px}}
li{{margin-bottom:8px}}
footer{{margin-top:60px;padding-top:24px;border-top:1px solid var(--rule);
  font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--ink-3)}}
footer a{{color:var(--accent)}}
</style></head>
<body><div class="wrap">
<header>
  <div class="eyebrow">Agent engineering &middot; August 2026</div>
  {header}
</header>
<article>{body}</article>
<footer>
  Praetor is open source: <a href="https://github.com/G-ojies/praetor">github.com/G-ojies/praetor</a><br>
  Live console: <a href="https://praetor-519854598879.us-central1.run.app">praetor-519854598879.us-central1.run.app</a><br>
  Built for the All Things Agentic Hackathon &middot; #AllThingsAgentic
</footer>
</div></body></html>
"""


def main() -> int:
    text = SOURCE.read_text()

    # Split the hand-written header (title + standfirst) from the body, so the
    # template can style them differently from the article proper.
    lines = text.splitlines()
    title = lines[0].lstrip("# ").strip()
    standfirst = next((l.strip("* ").strip() for l in lines[1:6] if l.startswith("*")), "")
    body_start = next(i for i, l in enumerate(lines) if l.strip() == "---") + 1
    body_md = "\n".join(lines[body_start:])

    md = markdown.Markdown(extensions=["extra", "sane_lists", "smarty"])
    header = f"<h1>{title}</h1>\n<p class=\"standfirst\">{standfirst}</p>"
    html = TEMPLATE.format(header=header, body=md.convert(body_md))

    OUT.write_text(html)
    (ROOT / "docs" / ".nojekyll").write_text("")
    print(f"  {OUT.relative_to(ROOT)}  {len(html):,} bytes")
    print(f"  title: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Wrap src/page.html (the page fragment, also published as the claude.ai artifact)
into a full HTML document at docs/index.html for GitHub Pages."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARK = "<!-- page -->"

frag = (ROOT / "src" / "page.html").read_text(encoding="utf-8")
head, sep, body = frag.partition(MARK)
if not sep:
    raise SystemExit(f"src/page.html is missing the {MARK} marker")
head = head.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")

doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f2f4f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0d1014" media="(prefers-color-scheme: dark)">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px);color-scheme:light}}body{{margin:0;font:14px system-ui,sans-serif}}img{{max-width:100%}}[hidden]{{display:none!important}}</style>
{head.strip()}
</head>
<body>
{body.strip()}
</body>
</html>
"""
out = ROOT / "docs" / "index.html"
out.write_text(doc, encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)} ({len(doc.encode('utf-8'))} bytes)")

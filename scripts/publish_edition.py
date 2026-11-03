#!/usr/bin/env python3
"""Check a draft edition and publish it into docs/data/.

Usage:
  python3 scripts/publish_edition.py DRAFT.json [--db-out DIR]

What it does:
  * validates the draft against the edition format in EDITOR_GUIDE.md,
    fixing what it safely can (ids, issue number, front-page picks, trimming)
    and refusing what it can't (bad date, stories without a source, no stories);
  * writes docs/data/<date>.json and updates docs/data/index.json;
  * with --db-out DIR, also writes DIR/edition.json and DIR/issue.json, the two
    documents the claude.ai version of the site reads from its database.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DESKS = ["ai", "games", "policy", "business", "buzz"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIRST_ISSUE = dt.date(2026, 11, 3)
MAX_BYTES = 240 * 1024  # the artifact database caps one document at 256 KiB


def text(v):
    return v.strip() if isinstance(v, str) else ""


def good_url(u):
    try:
        p = urlparse(u)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc) and " " not in u


def pairs(items, a, b, limit):
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and text(it.get(a)) and text(it.get(b)):
            out.append({a: text(it.get(a)), b: text(it.get(b))})
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft")
    ap.add_argument("--db-out")
    args = ap.parse_args()

    errors, warnings = [], []
    try:
        ed = json.loads(pathlib.Path(args.draft).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: cannot read {args.draft}: {e}")
    if not isinstance(ed, dict):
        sys.exit("ERROR: the draft must be a JSON object")

    date = text(ed.get("date"))
    if not DATE_RE.match(date):
        sys.exit("ERROR: date must look like 2026-11-04")
    day = dt.date.fromisoformat(date)
    win = ed.get("window") if isinstance(ed.get("window"), dict) else {}
    wfrom = text(win.get("from")) if DATE_RE.match(text(win.get("from"))) else (day - dt.timedelta(days=1)).isoformat()
    wto = text(win.get("to")) if DATE_RE.match(text(win.get("to"))) else date
    oldest_ok = dt.date.fromisoformat(wfrom) - dt.timedelta(days=2)

    # ---- stories
    sections_in = ed.get("sections") if isinstance(ed.get("sections"), dict) else {}
    unknown = sorted(set(sections_in) - set(DESKS))
    if unknown:
        warnings.append(f"ignored unknown sections: {', '.join(unknown)}")
    sections, remap = {}, {}
    for desk in DESKS:
        kept = []
        for i, s in enumerate(sections_in.get(desk) or []):
            where = f"{desk}[{i}]"
            if not isinstance(s, dict):
                errors.append(f"{where}: not an object")
                continue
            title, summary = text(s.get("title")), text(s.get("summary"))
            if not title or not summary:
                errors.append(f"{where}: title and summary are required")
                continue
            sources = []
            for src in s.get("sources") or []:
                if isinstance(src, dict) and good_url(text(src.get("url"))):
                    sources.append({"name": text(src.get("name")) or urlparse(text(src.get("url"))).netloc, "url": text(src.get("url"))})
            if not sources:
                errors.append(f"{where} 「{title}」: needs at least one http(s) source")
                continue
            sdate = text(s.get("date"))
            if not DATE_RE.match(sdate):
                warnings.append(f"{where} 「{title}」: missing or bad date")
                sdate = ""
            elif dt.date.fromisoformat(sdate) < oldest_ok and desk != "games":
                warnings.append(f"{where} 「{title}」: dated {sdate}, older than the window")
            elif dt.date.fromisoformat(sdate) > day + dt.timedelta(days=10):
                warnings.append(f"{where} 「{title}」: dated {sdate}, far in the future")
            try:
                imp = int(s.get("importance", 3))
            except (TypeError, ValueError):
                imp = 3
            story = {
                "id": f"{desk}-{len(kept) + 1}",
                "title": title,
                "summary": summary,
                "why": text(s.get("why")),
                "kind": text(s.get("kind")),
                "region": text(s.get("region")),
                "date": sdate,
                "importance": max(1, min(5, imp)),
                "status": "confirmed" if s.get("status") == "confirmed" else "reported",
                "facts": pairs(s.get("facts"), "label", "value", 6),
                "sides": pairs(s.get("sides"), "label", "view", 3),
                "sources": sources[:4],
            }
            if not story["why"]:
                warnings.append(f"{where} 「{title}」: no 看点 (why)")
            old_id = text(s.get("id"))
            if old_id:
                remap[old_id] = story["id"]
            kept.append(story)
        sections[desk] = kept

    total = sum(len(v) for v in sections.values())
    if total == 0:
        errors.append("the edition has no usable stories")
    for desk in DESKS:
        if not sections[desk]:
            warnings.append(f"section {desk} is empty")

    # ---- front page
    by_id = {s["id"]: s for v in sections.values() for s in v}
    lead = remap.get(text(ed.get("lead")), text(ed.get("lead")))
    if lead not in by_id and by_id:
        lead = max(by_id.values(), key=lambda s: s["importance"])["id"]
        warnings.append(f"lead missing or unknown; picked {lead}")
    top = []
    for t in ed.get("top") or []:
        t = remap.get(text(t), text(t))
        if t in by_id and t != lead and t not in top:
            top.append(t)
    if len(top) < 4:
        for s in sorted(by_id.values(), key=lambda s: -s["importance"]):
            if len(top) >= 4:
                break
            if s["id"] != lead and s["id"] not in top:
                top.append(s["id"])
    top = top[:4]

    overview = text(ed.get("overview"))
    if not overview:
        warnings.append("no overview (今日速览)")

    if errors:
        print("Refused. Fix these and run again:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)

    # ---- issue number and index
    DATA.mkdir(parents=True, exist_ok=True)
    index_path = DATA / "index.json"
    index = {"issues": []}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    issues = [i for i in index.get("issues", []) if isinstance(i, dict) and DATE_RE.match(text(i.get("date")))]
    existing = next((i for i in issues if i["date"] == date), None)
    if existing and existing.get("no"):
        no = int(existing["no"])
    else:
        no = max([int(i.get("no") or 0) for i in issues] + [(day - FIRST_ISSUE).days]) + 1

    published_at = text(ed.get("published_at")) or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    edition = {
        "date": date,
        "no": no,
        "window": {"from": wfrom, "to": wto},
        "published_at": published_at,
        "overview": overview,
        "lead": lead,
        "top": top,
        "sections": sections,
    }
    if text(ed.get("note")):
        edition["note"] = text(ed.get("note"))
    body = json.dumps(edition, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_BYTES:
        sys.exit(f"ERROR: edition is {len(body.encode('utf-8'))} bytes; trim it below {MAX_BYTES}")

    issue = {"date": date, "no": no, "lead_title": by_id[lead]["title"] if lead in by_id else "", "count": total}
    issues = [i for i in issues if i["date"] != date] + [issue]
    issues.sort(key=lambda i: i["date"], reverse=True)
    (DATA / f"{date}.json").write_text(body, encoding="utf-8")
    index_path.write_text(json.dumps({"updated": published_at, "issues": issues}, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.db_out:
        out = pathlib.Path(args.db_out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "edition.json").write_text(body, encoding="utf-8")
        (out / "issue.json").write_text(json.dumps(issue, ensure_ascii=False), encoding="utf-8")

    for w in warnings:
        print("warning: " + w)
    counts = ", ".join(f"{d} {len(sections[d])}" for d in DESKS)
    print(f"published 第{no}期 {date}: {total} stories ({counts}); lead {lead}; top {', '.join(top)}")
    print(f"wrote docs/data/{date}.json ({len(body.encode('utf-8'))} bytes) and docs/data/index.json")


if __name__ == "__main__":
    main()

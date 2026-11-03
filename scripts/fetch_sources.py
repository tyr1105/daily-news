#!/usr/bin/env python3
"""采集层：抓取各平台热榜和权威媒体 RSS，生成当天的候选新闻清单。

Runs in GitHub Actions (open internet), every morning before the daily editor.
Writes into OUT_DIR:
  latest.json   every item with source, rank, time, link and heat numbers
  cross.md      stories reported by several sources (the strongest "big news" signal)
  desk-<desk>.md  per-section candidate lists (ai, games, policy, business, buzz)
  status.md     which sources worked

Usage: python3 scripts/fetch_sources.py OUT_DIR
Needs: feedparser (pip install feedparser)
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

SGT = dt.timezone(dt.timedelta(hours=8))
UA = "wulan-daily/1.0 (+https://github.com/tyr1105/daily-news)"
TIMEOUT = 25
RSS_HOURS = 36           # RSS items older than this are left out
PER_SOURCE_MD = 20       # items per source in the desk files
DESKS = {
    "ai": "前沿 AI",
    "games": "游戏",
    "policy": "政治与经济",
    "business": "商业与产品",
    "buzz": "热议",
}

# ---- sources --------------------------------------------------------------
# Chinese hot lists through the 60s open API (https://github.com/vikiboss/60s),
# a free API collection published for developers. (path, name, desks)
SIXTY = [
    ("weibo", "微博热搜", ["buzz"]),
    ("zhihu", "知乎热榜", ["buzz"]),
    ("douyin", "抖音热搜", ["buzz"]),
    ("bili", "B站热搜", ["buzz", "games"]),
    ("toutiao", "今日头条热榜", ["buzz", "policy"]),
    ("baidu/hot", "百度热搜", ["buzz"]),
    ("rednote", "小红书热点", ["buzz"]),
    ("it-news", "IT资讯", ["ai", "business"]),
    ("ai-news", "AI资讯", ["ai"]),
]

# RSS / Atom feeds from established outlets and official blogs. (url, name, desks)
FEEDS = [
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World", ["policy"]),
    ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC Business", ["policy", "business"]),
    ("https://feeds.npr.org/1001/rss.xml", "NPR", ["policy"]),
    ("https://www.theguardian.com/world/rss", "The Guardian", ["policy"]),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera", ["policy"]),
    ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "CNBC", ["policy", "business"]),
    ("https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "CNA", ["policy"]),
    ("https://www.straitstimes.com/news/world/rss.xml", "海峡时报·国际", ["policy"]),
    ("https://www.straitstimes.com/news/singapore/rss.xml", "海峡时报·新加坡", ["policy"]),
    ("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", "BBC 中文", ["policy"]),
    ("https://www.theverge.com/rss/index.xml", "The Verge", ["ai", "business"]),
    ("https://techcrunch.com/feed/", "TechCrunch", ["ai", "business"]),
    ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica", ["ai"]),
    ("https://www.technologyreview.com/feed/", "MIT Technology Review", ["ai"]),
    ("https://www.wired.com/feed/rss", "WIRED", ["ai", "buzz"]),
    ("https://openai.com/news/rss.xml", "OpenAI", ["ai"]),
    ("https://deepmind.google/blog/rss.xml", "Google DeepMind", ["ai"]),
    ("https://blog.google/technology/ai/rss/", "Google AI", ["ai"]),
    ("https://huggingface.co/blog/feed.xml", "Hugging Face", ["ai"]),
    ("https://www.nature.com/nature.rss", "Nature", ["ai"]),
    ("https://www.science.org/rss/news_current.xml", "Science", ["ai"]),
    ("https://www.qbitai.com/feed", "量子位", ["ai"]),
    ("https://www.jiqizhixin.com/rss", "机器之心", ["ai"]),
    ("https://www.ithome.com/rss/", "IT之家", ["ai", "business"]),
    ("https://sspai.com/feed", "少数派", ["business"]),
    ("https://www.ifanr.com/feed", "爱范儿", ["business"]),
    ("https://www.huxiu.com/rss/0.xml", "虎嗅", ["business"]),
    ("https://www.chinanews.com.cn/rss/scroll-news.xml", "中国新闻网", ["policy"]),
    ("https://techcrunch.com/category/startups/feed/", "TechCrunch Startups", ["business"]),
    ("https://news.crunchbase.com/feed/", "Crunchbase News", ["business"]),
    ("https://www.producthunt.com/feed", "Product Hunt", ["business"]),
    ("https://www.gcores.com/rss", "机核", ["games"]),
    ("https://feeds.feedburner.com/ign/all", "IGN", ["games"]),
    ("https://www.gamespot.com/feeds/news/", "GameSpot", ["games"]),
    ("https://www.gematsu.com/feed", "Gematsu", ["games"]),
    ("https://www.eurogamer.net/feed", "Eurogamer", ["games"]),
    ("https://www.pcgamer.com/rss/", "PC Gamer", ["games"]),
    ("https://www.polygon.com/rss/index.xml", "Polygon", ["games"]),
    ("https://www.nintendolife.com/feeds/latest", "Nintendo Life", ["games"]),
    ("https://www.pushsquare.com/feeds/latest", "Push Square", ["games"]),
    ("https://www.videogameschronicle.com/feed/", "VGC", ["games"]),
    ("https://trends.google.com/trending/rss?geo=US", "Google 趋势·美国", ["buzz"]),
    ("https://trends.google.com/trending/rss?geo=SG", "Google 趋势·新加坡", ["buzz"]),
]


# ---- helpers --------------------------------------------------------------
def get(url, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def get_json(url):
    return json.loads(get(url, "application/json").decode("utf-8", "replace"))


def clean(text, limit=None):
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def iso(ts):
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if ts else None


def from_epoch(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1e12:  # milliseconds
        v /= 1000
    try:
        return dt.datetime.fromtimestamp(v, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def from_iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def item(source, rank, title, url, desks, when=None, heat=None, note=None):
    return {
        "source": source,
        "rank": rank,
        "title": clean(title, 200),
        "url": url or None,
        "time": iso(when),
        "heat": heat or None,
        "note": clean(note, 120) or None,
        "desks": desks,
    }


# ---- fetchers -------------------------------------------------------------
def first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def when_of(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return from_epoch(v)
    t = from_iso(v)
    if t and t.tzinfo is None:
        t = t.replace(tzinfo=SGT)
    return t


def fetch_sixty(path, name, desks):
    data = get_json(f"https://60s.viki.moe/v2/{path}")
    rows = data.get("data")
    if isinstance(rows, dict):
        rows = first(rows, "list", "items", "news", "data") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"no list in response (code {data.get('code')})")
    out = []
    for i, r in enumerate(rows[:40], 1):
        if isinstance(r, str):
            out.append(item(name, i, r, None, desks))
            continue
        heat = first(r, "hot_value_desc", "hot_value", "hot", "score", "heat")
        heat = clean(heat, 30) if heat is not None else None
        when = when_of(first(r, "created", "created_at", "event_time", "pub_time", "time", "date"))
        out.append(item(name, i, first(r, "title", "name", "word", "keyword") or "", first(r, "link", "url", "href"), desks, when, heat,
                        first(r, "desc", "description", "detail", "summary")))
    return out


def fetch_v2ex():
    rows = get_json("https://www.v2ex.com/api/topics/hot.json")
    return [item("V2EX 热门", i, r.get("title"), r.get("url"), ["buzz", "ai"], from_epoch(r.get("created")), f"{r.get('replies', 0)} 回复")
            for i, r in enumerate(rows if isinstance(rows, list) else [], 1)]


def fetch_feed(url, name, desks, now):
    import feedparser

    parsed = feedparser.parse(get(url, "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"))
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unreadable feed: {parsed.bozo_exception}")
    cutoff = now - dt.timedelta(hours=RSS_HOURS)
    rows = []
    for e in parsed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        when = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None
        if when and when < cutoff:
            continue
        heat = e.get("ht_approx_traffic")  # Google Trends
        note = e.get("ht_news_item_title") or e.get("summary")
        link = e.get("link") or e.get("ht_news_item_url")
        rows.append((when, e.get("title"), link, heat, note))
    rows.sort(key=lambda r: r[0] or now, reverse=True)
    return [item(name, i, t, l, desks, w, h, n) for i, (w, t, l, h, n) in enumerate(rows[:40], 1)]


def fetch_hn():
    data = get_json("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40")
    out = []
    hits = sorted(data.get("hits") or [], key=lambda h: -(h.get("points") or 0))
    for i, h in enumerate(hits, 1):
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        heat = f"{h.get('points', 0)} 分 · {h.get('num_comments', 0)} 评论"
        out.append(item("Hacker News", i, h.get("title"), link, ["ai", "business", "buzz"], from_iso(h.get("created_at")), heat,
                        f"讨论：https://news.ycombinator.com/item?id={h.get('objectID')}"))
    return out


def fetch_hf_papers():
    data = get_json("https://huggingface.co/api/daily_papers?limit=40")
    rows = []
    for d in data if isinstance(data, list) else []:
        p = d.get("paper") or {}
        rows.append((p.get("upvotes") or 0, d.get("title") or p.get("title"), p.get("id"), from_iso(d.get("publishedAt") or p.get("publishedAt"))))
    rows.sort(key=lambda r: -r[0])
    return [item("Hugging Face 每日论文", i, t, f"https://huggingface.co/papers/{pid}" if pid else None, ["ai"], w, f"{up} 赞")
            for i, (up, t, pid, w) in enumerate(rows, 1)]


def fetch_steam():
    data = get_json("https://store.steampowered.com/api/featuredcategories?cc=sg&l=english")
    out = []
    for key, label in (("top_sellers", "Steam 热销"), ("new_releases", "Steam 新品")):
        for i, g in enumerate((data.get(key) or {}).get("items") or [], 1):
            gid = g.get("id")
            price = g.get("final_price")
            heat = f"S${price / 100:.2f}" if isinstance(price, int) and price > 0 else None
            out.append(item(label, i, g.get("name"), f"https://store.steampowered.com/app/{gid}/" if gid else None, ["games"], None, heat))
    return out


def fetch_60s():
    data = get_json("https://60s.viki.moe/v2/60s").get("data") or {}
    link = data.get("link")
    news = data.get("news") or []
    when = None
    if data.get("date"):
        try:
            when = dt.datetime.fromisoformat(str(data["date"])).replace(tzinfo=SGT)
        except ValueError:
            when = None
    return [item("每天60秒读懂世界", i, n if isinstance(n, str) else (n.get("title") if isinstance(n, dict) else ""), link, ["policy", "buzz"], when)
            for i, n in enumerate(news, 1)]


# ---- clustering: the same story seen by several sources ------------------
STOP = set("""a an the and or of to in on for with from by at as is are was were be been this that these those it its into over after
than about new says say said will can could may might more most how why what when who amid vs via you your our their his her
""".split())


def tokens(title):
    t = title.lower()
    words = {w for w in re.findall(r"[a-z0-9][a-z0-9'\-]{2,}", t) if w not in STOP}
    cjk = re.findall(r"[一-鿿]", t)
    grams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words | grams


def clusters(items):
    toks = [tokens(x["title"]) for x in items]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index = {}
    for i, ts in enumerate(toks):
        for tk in ts:
            index.setdefault(tk, []).append(i)
    for i, ts in enumerate(toks):
        if len(ts) < 3:
            continue
        seen = set()
        for tk in ts:
            for j in index[tk]:
                if j <= i or j in seen or items[j]["source"] == items[i]["source"]:
                    continue
                seen.add(j)
                other = toks[j]
                if len(other) < 3:
                    continue
                inter = len(ts & other)
                if inter >= 3 and inter / len(ts | other) >= 0.34:
                    parent[find(i)] = find(j)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(items[i])
    out = []
    for g in groups.values():
        sources = {x["source"] for x in g}
        if len(sources) < 2:
            continue
        best = min(x["rank"] for x in g)
        out.append({"sources": len(sources), "best_rank": best, "desks": sorted({d for x in g for d in x["desks"]}), "items": g})
    out.sort(key=lambda c: (-c["sources"], c["best_rank"]))
    return out


# ---- output ---------------------------------------------------------------
def sgt(iso_time):
    if not iso_time:
        return ""
    return from_iso(iso_time).astimezone(SGT).strftime("%m-%d %H:%M")


def line(x):
    bits = [f"{x['rank']}. {x['title']}"]
    meta = [m for m in (x.get("heat"), sgt(x.get("time"))) if m]
    if meta:
        bits.append(f"（{' · '.join(meta)}）")
    if x.get("url"):
        bits.append(f" {x['url']}")
    if x.get("note") and not str(x["note"]).startswith("讨论："):
        bits.append(f"\n   {x['note']}")
    elif x.get("note"):
        bits.append(f"  {x['note']}")
    return "".join(bits)


def build(results, now, out):
    out.mkdir(parents=True, exist_ok=True)
    items = [x for r in results for x in r["items"] if x["title"]]
    groups = clusters(items)
    stamp = now.astimezone(SGT).strftime("%Y-%m-%d %H:%M")
    ok = [r for r in results if r["ok"]]
    head = (f"抓取时间：{stamp}（新加坡时间）。RSS 只保留最近 {RSS_HOURS} 小时，热榜是抓取时的快照。"
            f"成功 {len(ok)}/{len(results)} 个来源，共 {len(items)} 条。\n")

    (out / "latest.json").write_text(json.dumps({
        "fetched_at": iso(now),
        "rss_hours": RSS_HOURS,
        "sources": [{k: r[k] for k in ("name", "kind", "desks", "ok", "count", "error")} for r in results],
        "items": items,
        "clusters": [{"sources": c["sources"], "best_rank": c["best_rank"], "desks": c["desks"],
                      "titles": [f"{x['source']} #{x['rank']}：{x['title']}" for x in c["items"]],
                      "urls": [x["url"] for x in c["items"] if x["url"]]} for c in groups],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# 多源同报 · {stamp}", "", head,
             "同一件事被几个不同来源同时报道或上榜，来源越多，越可能是当天的大新闻。按来源数排序。", ""]
    for n, c in enumerate(groups[:80], 1):
        desks = "、".join(DESKS[d] for d in c["desks"])
        lines.append(f"## {n}. {c['sources']} 个来源 · 栏目：{desks}")
        for x in c["items"][:8]:
            lines.append(f"- {x['source']} #{x['rank']}：{x['title']}" + (f" {x['url']}" if x["url"] else ""))
        lines.append("")
    if not groups:
        lines.append("今天没有找到被多个来源同时报道的条目。")
    (out / "cross.md").write_text("\n".join(lines), encoding="utf-8")

    for desk, label in DESKS.items():
        lines = [f"# 候选清单 · {label} · {stamp}", "", head]
        for r in results:
            if desk not in r["desks"] or not r["ok"] or not r["items"]:
                continue
            lines.append(f"## {r['name']}（{'热榜排名' if r['kind'] == 'hotlist' else '最新在前'}）")
            lines.extend(line(x) for x in r["items"][:PER_SOURCE_MD])
            lines.append("")
        (out / f"desk-{desk}.md").write_text("\n".join(lines), encoding="utf-8")

    status = [f"# 来源状态 · {stamp}", "", "| 来源 | 类型 | 条数 | 状态 |", "|---|---|---|---|"]
    for r in results:
        status.append(f"| {r['name']} | {r['kind']} | {r['count']} | {'正常' if r['ok'] else '失败：' + (r['error'] or '')[:80]} |")
    (out / "status.md").write_text("\n".join(status) + "\n", encoding="utf-8")
    return items, groups


def run(job):
    name, kind, desks, fn = job
    try:
        rows = fn()
        return {"name": name, "kind": kind, "desks": desks, "ok": True, "count": len(rows), "error": None, "items": rows}
    except Exception as e:  # one broken source must not stop the rest
        msg = f"{type(e).__name__}: {e}"
        return {"name": name, "kind": kind, "desks": desks, "ok": False, "count": 0, "error": msg, "items": []}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = pathlib.Path(sys.argv[1])
    now = dt.datetime.now(dt.timezone.utc)
    jobs = [(name, "hotlist", desks, (lambda p=path, n=name, d=desks: fetch_sixty(p, n, d))) for path, name, desks in SIXTY]
    jobs += [(name, "rss", desks, (lambda u=url, n=name, d=desks: fetch_feed(u, n, d, now))) for url, name, desks in FEEDS]
    jobs += [
        ("Hacker News", "hotlist", ["ai", "business", "buzz"], fetch_hn),
        ("Hugging Face 每日论文", "hotlist", ["ai"], fetch_hf_papers),
        ("Steam 热销与新品", "hotlist", ["games"], fetch_steam),
        ("每天60秒读懂世界", "digest", ["policy", "buzz"], fetch_60s),
        ("V2EX 热门", "hotlist", ["buzz", "ai"], fetch_v2ex),
    ]
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(run, jobs))
    items, groups = build(results, now, out)
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} sources ok, {len(items)} items, {len(groups)} multi-source clusters")
    for r in bad:
        print(f"  failed: {r['name']}: {r['error']}")
    if len(bad) > len(results) // 2:
        sys.exit("more than half of the sources failed")


if __name__ == "__main__":
    main()

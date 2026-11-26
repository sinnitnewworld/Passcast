"""Scan public news for things that should make a drone pilot cautious in an area.

Sources: Google News RSS searches (India edition, English and Hindi) for
  - drone-ban / no-fly / temporary red zone orders by police or district magistrates
  - publicly announced visits of the PM, President, ministers and other dignitaries
  - air shows and fly-pasts
  - big gatherings: rallies, processions, melas, festivals, marathons

Output: site/events.json = {generated_utc, items:[{type, title, link, source, published_utc}]}.
The web apps match items to the pilot's spot by place name (city, district, state).

Deliberately NOT done: tracking or inferring the movements of protected persons
beyond what has been publicly announced. Only published headlines are used.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

QUERIES = [
    ("ban", "en", 'drone (ban OR banned OR prohibited OR "no-fly zone" OR "no fly zone" OR "red zone" OR "no drone zone" OR "drones prohibited")'),
    ("ban", "en", '("temporary red zone" OR "no flying zone" OR "Section 163" drone OR "Rule 24" drone)'),
    ("ban", "hi", "ड्रोन (प्रतिबंध OR रोक OR नो फ्लाई जोन OR रेड जोन)"),
    ("vip", "en", '("Prime Minister" OR "PM Modi" OR President OR "Vice President") visit (security OR traffic OR "no-fly" OR drone OR diversion)'),
    ("vip", "en", '("Chief Minister" OR "Home Minister" OR "Defence Minister") visit security arrangements'),
    ("airshow", "en", '("air show" OR airshow OR "Surya Kiran" OR "Sarang" OR flypast OR "fly-past" OR aerobatic)'),
    ("crowd", "en", '(rally OR procession OR mela OR yatra OR "Ganesh visarjan" OR marathon OR festival) ("traffic advisory" OR "traffic diversion" OR "security arrangements")'),
]
UA = "Mozilla/5.0 (PassCast events scanner; +https://sinnitnewworld.github.io/Passcast/)"
DRONE_WORDS = re.compile(r"drone|ड्रोन|uav|no[- ]fly|red zone|unmanned|paraglid|aerial", re.I)
# a visit only matters to pilots when it brings security measures; an air show only when it is a flying display
VIP_OK = re.compile(r"security|traffic|diversion|road closure|closed|restriction|no[- ]fly|drone|red zone|ban|prohibit|advisory|bandobast|cordon", re.I)
SHOW_OK = re.compile(r"air ?show|fly-?past|aerobatic|surya kiran|sarang|display|rehearsal", re.I)
SHOW_NOT = re.compile(r"expands|exhibition space|preparations|review|registration|bookings|tickets? sale|stall", re.I)
CROWD_OK = re.compile(r"traffic|diversion|advisory|security|closed|restriction|crowd|lakh|devotees", re.I)


def relevant(kind, title):
    if kind == "ban":
        return bool(DRONE_WORDS.search(title))
    if kind == "vip":
        return bool(VIP_OK.search(title))
    if kind == "airshow":
        return bool(SHOW_OK.search(title)) and not SHOW_NOT.search(title)
    if kind == "crowd":
        return bool(CROWD_OK.search(title))
    return True


def _url(q, lang):
    hl, ceid = ("hi", "IN:hi") if lang == "hi" else ("en-IN", "IN:en")
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:3d", "hl": hl, "gl": "IN", "ceid": ceid})


def parse_rss(xml_text, kind):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        source = src.text.strip() if src is not None and src.text else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        pub = it.findtext("pubDate")
        try:
            t = email.utils.parsedate_to_datetime(pub).astimezone(dt.timezone.utc)
        except Exception:
            t = None
        if not relevant(kind, title):
            continue
        out.append({"type": kind, "title": title, "link": link, "source": source,
                    "published_utc": t.strftime("%Y-%m-%dT%H:%MZ") if t else None})
    return out


def scan(fetch=None, max_age_h=72):
    fetch = fetch or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20).read().decode("utf-8", "replace"))
    items, seen, errors = [], set(), 0
    now = dt.datetime.now(dt.timezone.utc)
    for kind, lang, q in QUERIES:
        try:
            got = parse_rss(fetch(_url(q, lang)), kind)
        except Exception:
            errors += 1
            continue
        for x in got:
            key = re.sub(r"\W+", " ", x["title"].lower()).strip()[:90]
            if key in seen:
                continue
            if x["published_utc"]:
                age = (now - dt.datetime.strptime(x["published_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
                if age > max_age_h:
                    continue
            seen.add(key)
            items.append(x)
    order = {"ban": 0, "vip": 1, "airshow": 2, "crowd": 3}
    items.sort(key=lambda x: x["published_utc"] or "", reverse=True)     # newest first ...
    items.sort(key=lambda x: order[x["type"]])                             # ... within each type
    return {"generated_utc": now.strftime("%Y-%m-%dT%H:%MZ"), "queries": len(QUERIES), "errors": errors, "items": items}


def write(outdir):
    res = scan()
    p = Path(outdir) / "events.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False))
    print(f"  events: {len(res['items'])} items ({res['errors']} failed searches)")
    return res

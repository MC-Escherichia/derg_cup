#!/usr/bin/env python3
"""
fetch_results.py — pull World Cup group-stage results into pool.json.

Source: openfootball/worldcup.json (public domain, no API key). Run this before
the simulator; in CI it makes the board self-updating with no manual scoring.

It only writes the group stage into pool.json's "results" (leaving players,
groups, koResults, etc. untouched). Knockouts stay manual, because mapping them
to bracket match numbers depends on how the groups finish. Teams are matched by
country identity (ISO code), so spelling differences like "Czech Republic" vs
"Czechia" still line up; anything it can't match is reported, not guessed.

Usage:  python fetch_results.py [pool.json] [source_url]
"""
import json
import sys
import unicodedata
import urllib.request

SOURCE = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"

# country name (accent-free, lowercase) -> ISO-3166 alpha-2, used as a join key
_ISO = {
    "argentina":"AR","australia":"AU","austria":"AT","belgium":"BE","bolivia":"BO",
    "bosnia and herzegovina":"BA","bosnia & herzegovina":"BA","bosnia":"BA","brazil":"BR","cameroon":"CM","canada":"CA",
    "cape verde":"CV","cabo verde":"CV","colombia":"CO","costa rica":"CR","croatia":"HR","curacao":"CW",
    "czechia":"CZ","czech republic":"CZ","denmark":"DK","dr congo":"CD","congo dr":"CD",
    "democratic republic of the congo":"CD","ecuador":"EC","egypt":"EG","england":"_ENG",
    "france":"FR","germany":"DE","ghana":"GH","greece":"GR","haiti":"HT","honduras":"HN",
    "iran":"IR","ir iran":"IR","italy":"IT","ivory coast":"CI","cote d'ivoire":"CI",
    "jamaica":"JM","japan":"JP","jordan":"JO","mexico":"MX","morocco":"MA","netherlands":"NL",
    "new caledonia":"NC","new zealand":"NZ","nigeria":"NG","norway":"NO","panama":"PA",
    "paraguay":"PY","peru":"PE","poland":"PL","portugal":"PT","qatar":"QA","saudi arabia":"SA",
    "scotland":"_SCO","senegal":"SN","serbia":"RS","slovakia":"SK","south africa":"ZA",
    "south korea":"KR","korea republic":"KR","korea":"KR","spain":"ES","sweden":"SE",
    "switzerland":"CH","tunisia":"TN","turkey":"TR","turkiye":"TR","ukraine":"UA",
    "united states":"US","usa":"US","uruguay":"UY","uzbekistan":"UZ","wales":"_WAL",
}

def _canon(name):
    """Accent-free lowercase key, mapped to ISO when known (so spellings match)."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().strip().lower()
    return _ISO.get(s, s)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "wc-pool-sim"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main(pool_path="pool.json", url=SOURCE):
    pool = json.load(open(pool_path, encoding="utf-8"))
    groups = pool["groups"]
    canon_to_team = {_canon(t): t for gt in groups.values() for t in gt}
    team_group    = {t: g for g, gt in groups.items() for t in gt}

    try:
        data = fetch(url)
    except Exception as e:                         # network/source hiccup
        print(f"[fetch] could not reach source ({e}); leaving pool.json unchanged")
        return
    results, seen, unmatched, mismatched = [], set(), set(), 0
    schedule = {}                                  # team -> [{date, time, opponent}]
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("ft")
        t1 = canon_to_team.get(_canon(m.get("team1", "")))
        t2 = canon_to_team.get(_canon(m.get("team2", "")))

        # --- upcoming fixtures (no final score yet) -> schedule -------------
        if not ft and m.get("date"):
            for me, opp_raw in ((t1, m.get("team2")), (t2, m.get("team1"))):
                if not me:
                    continue
                opp = canon_to_team.get(_canon(opp_raw), opp_raw)
                schedule.setdefault(me, []).append(
                    {"date": m["date"], "time": m.get("time"), "opponent": opp})

        # --- finished group-stage games -> results -------------------------
        if not m.get("group") or not ft:           # only finished group-stage games
            continue
        if not t1:
            unmatched.add(m["team1"])
        if not t2:
            unmatched.add(m["team2"])
        if not t1 or not t2:
            continue
        if team_group[t1] != team_group[t2]:      # your draw disagrees with source
            mismatched += 1
            continue
        key = (team_group[t1], frozenset({t1, t2}))
        if key in seen:
            continue
        seen.add(key)
        results.append({"group": team_group[t1], "home": t1, "away": t2,
                        "score": [int(ft[0]), int(ft[1])]})

    for fixtures in schedule.values():            # chronological, earliest first
        fixtures.sort(key=lambda f: (f["date"], f.get("time") or ""))

    pool["results"] = results
    pool["schedule"] = schedule
    json.dump(pool, open(pool_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[fetch] wrote {len(results)} group result(s) and "
          f"{sum(len(v) for v in schedule.values())} upcoming fixture(s) to {pool_path}")
    if mismatched:
        print(f"[fetch] {mismatched} game(s) skipped — teams sit in different groups "
              f"in your draw than in the source (check your 'groups').")
    if unmatched:
        print(f"[fetch] unmatched names (add to _ISO if these are yours): "
              f"{', '.join(sorted(unmatched))}")

if __name__ == "__main__":
    main(*sys.argv[1:3])

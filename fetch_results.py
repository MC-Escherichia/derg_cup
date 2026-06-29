#!/usr/bin/env python3
"""
fetch_results.py — pull World Cup group-stage and knockout results into pool.json.

Source: openfootball/worldcup.json (public domain, no API key). Run this before
the simulator; in CI it makes the board self-updating with no manual scoring.

Group-stage results go into pool.json's "results". Knockout results go into
"koResults" keyed by match number (73–104), in the format the simulator expects:
  [team1, g1, team2, g2]          — regulation or ET winner
  [team1, g1, team2, g2, winner]  — penalty shootout (ET goals as the score)

Teams are matched by country identity (ISO code), so spelling differences like
"Czech Republic" vs "Czechia" still line up; anything it can't match is
reported, not guessed.

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
    ko_results = {}                                # match_num (str) -> list
    for m in data.get("matches", []):
        score = m.get("score") or {}
        ft  = score.get("ft")
        et  = score.get("et")
        p   = score.get("p")
        t1  = canon_to_team.get(_canon(m.get("team1", "")))
        t2  = canon_to_team.get(_canon(m.get("team2", "")))

        # --- upcoming fixtures (no final score yet) -> schedule -------------
        if not ft and m.get("date"):
            for me, opp_raw in ((t1, m.get("team2")), (t2, m.get("team1"))):
                if not me:
                    continue
                opp = canon_to_team.get(_canon(opp_raw), opp_raw)
                schedule.setdefault(me, []).append(
                    {"date": m["date"], "time": m.get("time"), "opponent": opp})

        # --- finished knockout games -> koResults --------------------------
        # Placeholder names ("W73", "L101") appear before results are published; skip silently.
        _is_placeholder = lambda n: len(n) <= 5 and n[:1] in "WL" and n[1:].isdigit()
        if not m.get("group") and ft and m.get("num"):
            if not t1 and not _is_placeholder(m.get("team1", "")): unmatched.add(m["team1"])
            if not t2 and not _is_placeholder(m.get("team2", "")): unmatched.add(m["team2"])
            if t1 and t2:
                if p:                              # went to penalties
                    goals = et or ft              # ET score is the match score
                    winner = t1 if p[0] > p[1] else t2
                    ko_results[str(m["num"])] = [t1, int(goals[0]), t2, int(goals[1]), winner]
                elif et and et[0] != et[1]:        # ET winner (no penalties)
                    ko_results[str(m["num"])] = [t1, int(et[0]), t2, int(et[1])]
                else:                              # regulation winner
                    ko_results[str(m["num"])] = [t1, int(ft[0]), t2, int(ft[1])]
            continue

        # --- finished group-stage games -> results -------------------------
        if not m.get("group") or not ft:
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

    pool["results"]   = results
    pool["schedule"]  = schedule
    pool["koResults"] = ko_results
    json.dump(pool, open(pool_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[fetch] wrote {len(results)} group result(s), "
          f"{len(ko_results)} KO result(s), and "
          f"{sum(len(v) for v in schedule.values())} upcoming fixture(s) to {pool_path}")
    if mismatched:
        print(f"[fetch] {mismatched} game(s) skipped — teams sit in different groups "
              f"in your draw than in the source (check your 'groups').")
    if unmatched:
        print(f"[fetch] unmatched names (add to _ISO if these are yours): "
              f"{', '.join(sorted(unmatched))}")

if __name__ == "__main__":
    main(*sys.argv[1:3])

"""
World Cup 2026 Pool — Monte Carlo simulator
===========================================

Simulates the remainder of the tournament many times to estimate each pool
player's probability of winning.

Format coded against (FIFA 2026):
  48 teams, 12 groups of 4. Top 2 per group + 8 best 3rd-placed -> Round of 32.
  R32 -> R16 -> QF -> SF -> Final (single elimination, ET then penalties).

Pool scoring (group stage AND knockouts):
  win = 3, draw / shootout loss = 1, outright (regulation/ET) loss = 0.
  Pool tiebreaker = cumulative goal differential across a player's 3 teams.

This file runs out of the box on SYNTHETIC data so you can see the pipeline
work, then you replace the four DATA blocks marked  # >>> REPLACE <<<.

The goal model is a Dixon-Coles corrected Poisson (the standard refinement of
the basic double-Poisson soccer model). Notes at the bottom show the upgrade
path to a real Bayesian/MCMC fit.
"""

import math
import random
import json
import os
from datetime import datetime, timezone
from collections import defaultdict
from functools import lru_cache
import numpy as np

rng = np.random.default_rng()

# ---------------------------------------------------------------------------
# CONFIG — the two judgment calls your pool rules leave open
# ---------------------------------------------------------------------------
N_SIMS          = 20_000
PEN_WIN_POINTS  = 3      # winning a shootout: 3 (a win) or 1 (still a "tie")? <-- DECISION 1
PEN_LOSS_POINTS = 1      # losing a shootout (your rules: 1)
HOME_ADV        = 0.0    # neutral venues by default; set per-match if you want host edges
RHO             = -0.08  # Dixon-Coles low-score dependence (negative boosts 0-0 / 1-1)

# Evolve Elo from the pre-tournament snapshot using eloratings.net's own formula,
# replaying the results you've entered — so you only ever need the pre-WC ratings.
EVOLVE_ELO   = True
WC_K         = 60        # World Football Elo weight for World Cup matches
ELO_HOME_ADV = 0         # neutral sites at the WC; set 100 for a host playing at home

GLOBAL_BASE = math.log(1.35)   # avg goals/team baseline (~1.35); tune to taste
_MAXG = 15                     # goal grid cap for the Dixon-Coles joint distribution

# ===========================================================================
#  DATA BLOCK 1 — TEAM RATINGS                                  # >>> REPLACE <<<
#  Give every team an attack/defense rating in log-space (centered near 0).
#  Easiest source: World Football Elo (eloratings.net) or bookmaker-implied
#  strength -> run through elo_to_ratings() below.
# ===========================================================================
def elo_to_ratings(elo_dict, scale=450.0):   # 450 calibrated to Elo's win formula
    """Convert a single Elo-style strength into symmetric atk/def offsets."""
    mean = np.mean(list(elo_dict.values()))
    out = {}
    for t, e in elo_dict.items():
        z = (e - mean) / scale
        out[t] = {"atk": z, "def": z}   # stronger -> scores more AND concedes less
    return out

# --- World Football Elo update (eloratings.net): Rn = Ro + K*G*(W - We) --------
def _wfe_g(goal_diff):
    """Goal-difference index G."""
    n = abs(goal_diff)
    if n <= 1: return 1.0
    if n == 2: return 1.5
    return (11 + n) / 8.0          # 3 goals ->1.75, 4 ->1.875, ...

def _wfe_update(elo, a, b, ga, gb, home_a=0, home_b=0, k=WC_K):
    """Apply one result to the elo dict in place (zero-sum). A penalty shootout
    counts as a draw (pass ga == gb), as eloratings.net does."""
    dr = (elo[a] + ELO_HOME_ADV * home_a) - (elo[b] + ELO_HOME_ADV * home_b)
    we_a = 1.0 / (10 ** (-dr / 400.0) + 1.0)         # win expectancy
    wa = 1.0 if ga > gb else 0.0 if ga < gb else 0.5  # actual result
    delta = k * _wfe_g(ga - gb) * (wa - we_a)
    elo[a] += delta
    elo[b] -= delta

# ===========================================================================
#  DATA BLOCKS 2-4 — GROUPS, RESULTS-SO-FAR, PLAYER ASSIGNMENTS  # >>> REPLACE <<<
#  Filled with a synthetic 48-team setup below so the script runs.
# ===========================================================================

def build_synthetic_world():
    """Generates a complete fake tournament so you can see output immediately."""
    teams = [f"T{i:02d}" for i in range(1, 49)]
    elo = {t: 1500 + 600 * (1 - i / 48) + rng.normal(0, 30) for i, t in enumerate(teams)}
    ratings = elo_to_ratings(elo)

    # tiers by strength (for the draft): top16 / mid16 / bot16
    ranked = sorted(teams, key=lambda t: -elo[t])
    tiers = {"top": ranked[:16], "mid": ranked[16:32], "bot": ranked[32:]}

    # 12 groups of 4 (random here; replace with the real draw)
    shuffled = teams[:]
    random.shuffle(shuffled)
    groups = {chr(65 + g): shuffled[4 * g:4 * g + 4] for g in range(12)}

    # round-robin schedule per group; results=None means "not yet played"
    results = {}  # (group, frozenset{a,b}) -> (ga, gb) or None
    for g, gteams in groups.items():
        for i in range(4):
            for j in range(i + 1, 4):
                results[(g, frozenset({gteams[i], gteams[j]}))] = None

    # 16 players, each drafts one team per tier
    players = {}
    pools = {k: v[:] for k, v in tiers.items()}
    for k in pools:
        random.shuffle(pools[k])
    for p in range(16):
        players[f"Player{p+1:02d}"] = [pools["top"][p], pools["mid"][p], pools["bot"][p]]

    return ratings, groups, results, players

# ===========================================================================
#  FLAGS — team name -> country flag emoji (auto, for the board)
# ===========================================================================
# Flag emoji = the team's ISO-3166 alpha-2 code as regional-indicator letters.
# England/Scotland/Wales have no 2-letter flag, so they're literal subdivision
# emoji. Unknown names just render with no flag (and you can add an alias).
_SPECIAL_FLAGS = {"england": "🏴\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
                  "scotland": "🏴\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
                  "wales":    "🏴\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F"}
_ISO = {  # lowercase name (and common aliases) -> ISO-3166 alpha-2
    "argentina":"AR","australia":"AU","austria":"AT","belgium":"BE","bolivia":"BO",
    "bosnia and herzegovina":"BA","bosnia":"BA","brazil":"BR","cameroon":"CM","canada":"CA",
    "cape verde":"CV","colombia":"CO","costa rica":"CR","croatia":"HR","curacao":"CW",
    "curaçao":"CW","czechia":"CZ","czech republic":"CZ","denmark":"DK","dr congo":"CD",
    "democratic republic of the congo":"CD","ecuador":"EC","egypt":"EG","france":"FR",
    "germany":"DE","ghana":"GH","greece":"GR","haiti":"HT","honduras":"HN","iran":"IR",
    "italy":"IT","ivory coast":"CI","cote d'ivoire":"CI","côte d'ivoire":"CI","jamaica":"JM",
    "japan":"JP","jordan":"JO","mexico":"MX","morocco":"MA","netherlands":"NL",
    "new caledonia":"NC","new zealand":"NZ","nigeria":"NG","norway":"NO","panama":"PA",
    "paraguay":"PY","peru":"PE","poland":"PL","portugal":"PT","qatar":"QA",
    "saudi arabia":"SA","senegal":"SN","serbia":"RS","slovakia":"SK","south africa":"ZA",
    "south korea":"KR","korea republic":"KR","korea":"KR","spain":"ES","sweden":"SE",
    "switzerland":"CH","tunisia":"TN","turkey":"TR","türkiye":"TR","ukraine":"UA",
    "united states":"US","usa":"US","uruguay":"UY","uzbekistan":"UZ",
}
def iso_to_flag(iso2):
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso2.upper())
def flag(team):
    key = str(team).strip().lower()
    if key in _SPECIAL_FLAGS:
        return _SPECIAL_FLAGS[key]
    iso = _ISO.get(key)
    return iso_to_flag(iso) if iso else ""

# ===========================================================================
#  CONFIG LOADING — single source of truth is pool.json (+ ratings.json)
# ===========================================================================
def load_pool(pool_path="pool.json", ratings_path="ratings.json"):
    """Load the pool from pool.json. Elo comes from ratings.json (team->elo) if
    present, else a 'ratings' block in pool.json, else a neutral default."""
    cfg     = json.load(open(pool_path, encoding="utf-8"))
    title   = cfg.get("title", "World Cup Pool")
    groups  = cfg["groups"]                       # {"A": [4 teams], ...}
    players = cfg["players"]                       # {"Nick": [top, mid, bot]}
    third   = cfg.get("thirdPlaceOverride", {}) or {}
    ko      = {int(k): tuple(v) for k, v in (cfg.get("koResults", {}) or {}).items()}

    elo = {}
    if os.path.exists(ratings_path):
        elo = json.load(open(ratings_path, encoding="utf-8"))
    elif "ratings" in cfg:
        elo = dict(cfg["ratings"])
    all_teams = [t for gt in groups.values() for t in gt]
    missing = [t for t in all_teams if t not in elo]
    if missing:
        print(f"[warn] no Elo for {len(missing)} team(s); using 1500: "
              f"{', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}")
        for t in missing:
            elo[t] = 1500.0
    elo = {t: float(elo[t]) for t in all_teams}     # pre-tournament snapshot

    # build RESULTS: every group pairing starts None; fill in played scores,
    # oriented to the group's canonical order (how sim_group_stage reads them).
    results = {}
    for g, gt in groups.items():
        for i in range(len(gt)):
            for j in range(i + 1, len(gt)):
                results[(g, frozenset({gt[i], gt[j]}))] = None
    for r in cfg.get("results", []):
        g, a, b, sc = r["group"], r["home"], r["away"], r["score"]
        ga, gb = (sc[0], sc[1]) if groups[g].index(a) < groups[g].index(b) else (sc[1], sc[0])
        results[(g, frozenset({a, b}))] = (int(ga), int(gb))

    # Evolve Elo from the snapshot via the World Football Elo formula, replaying
    # played games in order (group games as entered, then knockouts by match no.).
    # Penalty shootouts (len-5 koResults) have equal goals -> counted as draws.
    if EVOLVE_ELO:
        played = 0
        for r in cfg.get("results", []):
            sc = r["score"]
            _wfe_update(elo, r["home"], r["away"], int(sc[0]), int(sc[1])); played += 1
        for m in sorted(ko):
            rec = ko[m]
            _wfe_update(elo, rec[0], rec[2], int(rec[1]), int(rec[3])); played += 1
        if played:
            print(f"[elo] evolved ratings from pre-tournament snapshot through "
                  f"{played} played match(es) using the eloratings.net formula (K={WC_K})")

    ratings = elo_to_ratings(elo)
    return title, ratings, groups, results, players, third, ko

def load_config():
    if os.path.exists("pool.json"):
        return load_pool()
    ratings, groups, results, players = build_synthetic_world()   # demo fallback
    return "World Cup Pool (demo data)", ratings, groups, results, players, {}, {}

TITLE, ratings, GROUPS, RESULTS, PLAYERS, THIRD_PLACE_OVERRIDE, KO_RESULTS = load_config()

# team -> owning player (a team belongs to exactly one player; unowned -> None)
TEAM_OWNER = {}
for player, teams_ in PLAYERS.items():
    for t in teams_:
        TEAM_OWNER[t] = player

# ---------------------------------------------------------------------------
#  MATCH MODEL — Dixon-Coles corrected Poisson goals
#  Plain double-Poisson gets aggregate draws ~right but mis-shapes low scores
#  (too few 0-0 / 1-1, too many 1-0 / 0-1). Dixon-Coles reweights exactly those
#  four cells via RHO, then we sample a scoreline from the corrected joint.
# ---------------------------------------------------------------------------
def expected_lambdas(a, b, home_a=0.0, home_b=0.0):
    ra, rb = ratings[a], ratings[b]
    la = math.exp(GLOBAL_BASE + HOME_ADV * home_a + ra["atk"] - rb["def"])
    lb = math.exp(GLOBAL_BASE + HOME_ADV * home_b + rb["atk"] - ra["def"])
    return la, lb

def _pois_pmf(lam):
    """P(0.._MAXG) for Poisson(lam) via stable recurrence (no factorials)."""
    p = np.empty(_MAXG + 1)
    p[0] = math.exp(-lam)
    for k in range(1, _MAXG + 1):
        p[k] = p[k - 1] * lam / k
    return p

def _dc_joint(la, lb):
    """Dixon-Coles corrected joint PMF over (goals_a, goals_b), normalized."""
    P = np.outer(_pois_pmf(la), _pois_pmf(lb))
    P[0, 0] *= max(0.0, 1 - la * lb * RHO)
    P[0, 1] *= max(0.0, 1 + la * RHO)
    P[1, 0] *= max(0.0, 1 + lb * RHO)
    P[1, 1] *= max(0.0, 1 - RHO)
    return P / P.sum()

@lru_cache(maxsize=None)
def _dc_cumdist(a, b, home_a=0.0, home_b=0.0):
    """Cached cumulative scoreline distribution for a matchup (fixtures repeat
    every sim, so this is computed once per pair). Clear cache if you change
    RHO / ratings at runtime: _dc_cumdist.cache_clear()."""
    la, lb = expected_lambdas(a, b, home_a, home_b)
    return np.cumsum(_dc_joint(la, lb).ravel())

def sim_goals(a, b, home_a=0.0, home_b=0.0):
    cum = _dc_cumdist(a, b, home_a, home_b)
    idx = min(int(np.searchsorted(cum, rng.random())), cum.size - 1)
    ga, gb = divmod(idx, _MAXG + 1)          # row-major: idx = ga*(MAXG+1) + gb
    return int(ga), int(gb)

def sim_extra_time(a, b):
    """~1/3 of a match worth of goals (plain Poisson; DC is a full-match fit)."""
    la, lb = expected_lambdas(a, b)
    return int(rng.poisson(la / 3)), int(rng.poisson(lb / 3))

def sim_penalties(a, b):
    """Mild edge to the stronger side, capped near 50/50."""
    diff = (ratings[a]["atk"] + ratings[a]["def"]) - (ratings[b]["atk"] + ratings[b]["def"])
    p_a = 0.5 + max(-0.12, min(0.12, diff * 0.05))
    return a if rng.random() < p_a else b

# ---------------------------------------------------------------------------
#  GROUP STAGE
# ---------------------------------------------------------------------------
def sim_group_stage(score):
    """Returns standings per group. `score` accumulates pool points + GD."""
    standings = {}
    for g, gteams in GROUPS.items():
        tbl = {t: {"pts": 0, "gf": 0, "ga": 0} for t in gteams}
        for i in range(4):
            for j in range(i + 1, 4):
                a, b = gteams[i], gteams[j]
                played = RESULTS[(g, frozenset({a, b}))]
                ga, gb = played if played is not None else sim_goals(a, b)
                tbl[a]["gf"] += ga; tbl[a]["ga"] += gb
                tbl[b]["gf"] += gb; tbl[b]["ga"] += ga
                # match points + group standings points
                if ga > gb:
                    tbl[a]["pts"] += 3; award(score, a, 3, ga - gb); award(score, b, 0, gb - ga)
                elif gb > ga:
                    tbl[b]["pts"] += 3; award(score, b, 3, gb - ga); award(score, a, 0, ga - gb)
                else:
                    tbl[a]["pts"] += 1; tbl[b]["pts"] += 1
                    award(score, a, 1, 0); award(score, b, 1, 0)
        ranked = sorted(gteams, key=lambda t: (tbl[t]["pts"],
                                               tbl[t]["gf"] - tbl[t]["ga"],
                                               tbl[t]["gf"]), reverse=True)
        standings[g] = [(t, tbl[t]) for t in ranked]
    return standings

# ---------------------------------------------------------------------------
#  BRACKET STRUCTURE (official FIFA 2026, matches 73-104)
#  Participant tokens:  ('GW',g)=group winner, ('GR',g)=runner-up,
#  ('3',w)=3rd-placed team assigned to the slot facing winner of group w,
#  ('W',m)=winner of match m, ('L',m)=loser of match m.
# ---------------------------------------------------------------------------
# Each third-place SLOT is identified by the group winner it faces, and lists
# the groups whose 3rd-placed team is eligible for it. (The winner's own group
# is excluded, so the no-group-rematch rule is built in.)
THIRD_SLOTS = {
    "E": ["A", "B", "C", "D", "F"],   # Match 74
    "I": ["C", "D", "F", "G", "H"],   # Match 77
    "A": ["C", "E", "F", "H", "I"],   # Match 79
    "L": ["E", "H", "I", "J", "K"],   # Match 80
    "D": ["B", "E", "F", "I", "J"],   # Match 81
    "G": ["A", "E", "H", "I", "J"],   # Match 82
    "B": ["E", "F", "G", "I", "J"],   # Match 85
    "K": ["D", "E", "I", "J", "L"],   # Match 87
}

KO_GRAPH = {
    73: (("GR","A"), ("GR","B")),   74: (("GW","E"), ("3","E")),
    75: (("GW","F"), ("GR","C")),   76: (("GW","C"), ("GR","F")),
    77: (("GW","I"), ("3","I")),    78: (("GR","E"), ("GR","I")),
    79: (("GW","A"), ("3","A")),    80: (("GW","L"), ("3","L")),
    81: (("GW","D"), ("3","D")),    82: (("GW","G"), ("3","G")),
    83: (("GR","K"), ("GR","L")),   84: (("GW","H"), ("GR","J")),
    85: (("GW","B"), ("3","B")),    86: (("GW","J"), ("GR","H")),
    87: (("GW","K"), ("3","K")),    88: (("GR","D"), ("GR","G")),
    89: (("W",74), ("W",77)),       90: (("W",73), ("W",75)),
    91: (("W",76), ("W",78)),       92: (("W",79), ("W",80)),
    93: (("W",83), ("W",84)),       94: (("W",81), ("W",82)),
    95: (("W",86), ("W",88)),       96: (("W",85), ("W",87)),
    97: (("W",89), ("W",90)),       98: (("W",93), ("W",94)),
    99: (("W",91), ("W",92)),      100: (("W",95), ("W",96)),
   101: (("W",97), ("W",98)),      102: (("W",99), ("W",100)),
   103: (("L",101), ("L",102)),    104: (("W",101), ("W",102)),  # 3rd place, final
}

def best_third_groups(standings):
    """The 8 groups whose 3rd-placed team qualifies (ranked pts, GD, GF)."""
    thirds = [(g, standings[g][2][1]) for g in GROUPS]
    thirds.sort(key=lambda x: (x[1]["pts"], x[1]["gf"] - x[1]["ga"], x[1]["gf"]),
                reverse=True)
    return {g for g, _ in thirds[:8]}

def assign_thirds(qualified):
    """Annex-C placement, solved as a bipartite matching: assign each qualifying
    3rd-place group to one winner-slot, respecting eligibility. Kuhn's algorithm
    always finds a perfect matching (FIFA guarantees one exists). When several
    valid matchings exist this picks one of them; to reproduce FIFA's exact
    published row for an ambiguous combo, override this with the Annex C table.
    Returns {winner_group -> third_place_group}."""
    cand = {s: [g for g in THIRD_SLOTS[s] if g in qualified] for s in THIRD_SLOTS}
    slot_of_group, group_of_slot = {}, {}
    def augment(slot, seen):
        for g in cand[slot]:
            if g in seen:
                continue
            seen.add(g)
            if g not in slot_of_group or augment(slot_of_group[g], seen):
                slot_of_group[g] = slot
                group_of_slot[slot] = g
                return True
        return False
    for s in THIRD_SLOTS:
        augment(s, set())
    return group_of_slot

# ---------------------------------------------------------------------------
#  KNOCKOUT OVERRIDES — now set in pool.json, loaded above into
#  THIRD_PLACE_OVERRIDE and KO_RESULTS. For reference:
#    "thirdPlaceOverride": {"E":"C", "I":"H", ...}   # pin real 3rd-place slots
#    "koResults": {"77": ["Spain", 2, "Germany", 1],          # decided in play
#                  "74": ["France", 1, "Brazil", 1, "France"]} # decided on pens
#  Goals are regulation + extra time; shootout goals are NOT counted in GD.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  KNOCKOUTS
# ---------------------------------------------------------------------------
def apply_ko_result(rec, score):
    """Score a pre-recorded knockout result; returns (winner, loser).
    Mirrors sim_knockout's scoring exactly so locked and simulated games agree."""
    if len(rec) == 5:
        a, ga, b, gb, pen_w = rec
    else:
        a, ga, b, gb = rec
        pen_w = None
    if ga != gb:
        w, l = (a, b) if ga > gb else (b, a)
        award(score, w, 3, abs(ga - gb)); award(score, l, 0, -abs(ga - gb))
    else:                                   # went to penalties
        w = pen_w; l = b if w == a else a
        award(score, w, PEN_WIN_POINTS, 0); award(score, l, PEN_LOSS_POINTS, 0)
    return w, l

def sim_knockout(a, b, score):
    """Single match; returns (winner, loser). Awards pool points + GD."""
    ga, gb = sim_goals(a, b)
    if ga != gb:
        w, l = (a, b) if ga > gb else (b, a)
        award(score, w, 3, abs(ga - gb)); award(score, l, 0, -abs(ga - gb))
        return w, l
    # extra time
    ea, eb = sim_extra_time(a, b)
    ga += ea; gb += eb
    if ga != gb:
        w, l = (a, b) if ga > gb else (b, a)
        award(score, w, 3, abs(ga - gb)); award(score, l, 0, -abs(ga - gb))
        return w, l
    # penalties — shootout goals don't count toward GD
    w = sim_penalties(a, b)
    l = b if w == a else a
    award(score, w, PEN_WIN_POINTS, 0); award(score, l, PEN_LOSS_POINTS, 0)
    return w, l

def sim_knockouts(standings, score):
    """Resolve the bracket and play it out. Played matches in KO_RESULTS are
    locked in (not re-simulated); 3rd-place placement uses THIRD_PLACE_OVERRIDE
    if set, otherwise the Annex-C matching."""
    winners = {g: standings[g][0][0] for g in GROUPS}
    runners = {g: standings[g][1][0] for g in GROUPS}
    thirds  = {g: standings[g][2][0] for g in GROUPS}
    assign  = THIRD_PLACE_OVERRIDE or assign_thirds(best_third_groups(standings))
    res_w, res_l = {}, {}
    def resolve(spec):
        kind, val = spec
        if kind == "GW": return winners[val]
        if kind == "GR": return runners[val]
        if kind == "3":  return thirds[assign[val]]
        if kind == "W":  return res_w[val]
        if kind == "L":  return res_l[val]
    for m in range(73, 105):
        if m in KO_RESULTS:                              # already played -> lock in
            res_w[m], res_l[m] = apply_ko_result(KO_RESULTS[m], score)
        else:
            a = resolve(KO_GRAPH[m][0]); b = resolve(KO_GRAPH[m][1])
            res_w[m], res_l[m] = sim_knockout(a, b, score)
    return res_w[104]   # champion

# ---------------------------------------------------------------------------
#  SCORING BOOKKEEPING
# ---------------------------------------------------------------------------
def award(score, team, pts, gd):
    owner = TEAM_OWNER.get(team)
    if owner is not None:
        score[owner]["pts"] += pts
        score[owner]["gd"]  += gd

def sim_tournament():
    score = {p: {"pts": 0, "gd": 0} for p in PLAYERS}
    standings = sim_group_stage(score)
    sim_knockouts(standings, score)
    # winner of this simulated pool: most pts, then GD
    best = max(score.values(), key=lambda s: (s["pts"], s["gd"]))
    winners = [p for p, s in score.items()
               if s["pts"] == best["pts"] and s["gd"] == best["gd"]]
    return winners, score

# ---------------------------------------------------------------------------
#  MONTE CARLO
# ---------------------------------------------------------------------------
def matches_played():
    """Count of locked results (group + knockout) so the vis can show progress."""
    return sum(1 for v in RESULTS.values() if v is not None) + len(KO_RESULTS)

def calc_actual_score():
    """Calculate actual score from played matches (no simulation)."""
    score = {p: {"pts": 0, "gd": 0} for p in PLAYERS}

    # Score from group stage results via RESULTS dict
    # RESULTS is keyed by (group, frozenset({team1, team2})) with (goals_first, goals_second)
    # where "first" and "second" refer to the index order in GROUPS[group]
    for (g, teams_set), result in RESULTS.items():
        if result is None:
            continue
        ga, gb = result
        # Find which team is "first" and "second" based on group order
        group_teams = GROUPS[g]
        teams_list = list(teams_set)

        # Get indices of both teams in the group
        indices = {t: group_teams.index(t) for t in teams_list}
        a = min(teams_list, key=lambda t: indices[t])  # Team with lower index
        b = max(teams_list, key=lambda t: indices[t])  # Team with higher index

        if ga > gb:
            award(score, a, 3, ga - gb)
            award(score, b, 0, gb - ga)
        elif gb > ga:
            award(score, b, 3, gb - ga)
            award(score, a, 0, ga - gb)
        else:
            award(score, a, 1, 0)
            award(score, b, 1, 0)

    # Score from knockout results
    for rec in KO_RESULTS.values():
        if len(rec) == 5:
            a, ga, b, gb, pen_w = rec
        else:
            a, ga, b, gb = rec
            pen_w = None

        if ga > gb:
            award(score, a, PEN_WIN_POINTS if pen_w else 3, ga - gb)
            award(score, b, PEN_LOSS_POINTS if pen_w else 0, gb - ga)
        elif gb > ga:
            award(score, b, PEN_WIN_POINTS if pen_w else 3, gb - ga)
            award(score, a, PEN_LOSS_POINTS if pen_w else 0, ga - gb)
        else:
            award(score, a, 1, 0)
            award(score, b, 1, 0)

    return score

def actual_group_tables():
    """Per-team current group standing from PLAYED group matches only.

    Returns {team: {"group", "place", "w", "d", "l", "pts", "gd", "played"}}.
    Place is the team's current rank (1-4) within its group by pts, GD, GF.
    """
    info = {}
    for g, gteams in GROUPS.items():
        tbl = {t: {"pts": 0, "gf": 0, "ga": 0, "w": 0, "d": 0, "l": 0, "played": 0}
               for t in gteams}
        for i in range(len(gteams)):
            for j in range(i + 1, len(gteams)):
                a, b = gteams[i], gteams[j]
                played = RESULTS[(g, frozenset({a, b}))]
                if played is None:
                    continue
                ga, gb = played
                for t, gf, gainst in ((a, ga, gb), (b, gb, ga)):
                    tbl[t]["gf"] += gf; tbl[t]["ga"] += gainst; tbl[t]["played"] += 1
                if ga > gb:
                    tbl[a]["pts"] += 3; tbl[a]["w"] += 1; tbl[b]["l"] += 1
                elif gb > ga:
                    tbl[b]["pts"] += 3; tbl[b]["w"] += 1; tbl[a]["l"] += 1
                else:
                    tbl[a]["pts"] += 1; tbl[b]["pts"] += 1
                    tbl[a]["d"] += 1; tbl[b]["d"] += 1
        ranked = sorted(gteams, key=lambda t: (tbl[t]["pts"],
                                               tbl[t]["gf"] - tbl[t]["ga"],
                                               tbl[t]["gf"]), reverse=True)
        for place, t in enumerate(ranked, 1):
            d = tbl[t]
            info[t] = {"group": g, "place": place,
                       "w": d["w"], "d": d["d"], "l": d["l"],
                       "pts": d["pts"], "gd": d["gf"] - d["ga"], "played": d["played"]}
    return info

def run(n=N_SIMS, json_path=None, history_path=None):
    wins = defaultdict(float)
    pts_sum = defaultdict(float)
    gd_sum = defaultdict(float)
    for _ in range(n):
        winners, score = sim_tournament()
        share = 1.0 / len(winners)          # split ties in the pool
        for w in winners:
            wins[w] += share
        for p, s in score.items():
            pts_sum[p] += s["pts"]
            gd_sum[p] += s["gd"]
    rows = sorted(PLAYERS, key=lambda p: wins[p], reverse=True)

    # Calculate actual scores from played matches
    actual_score = calc_actual_score()

    print(f"\n{'Player':<10} {'Current':>8} {'Win %':>7} {'Avg pts':>8} {'Avg GD':>8}")
    print("-" * 50)
    for p in rows:
        print(f"{p:<10} {actual_score[p]['pts']:>8} {100*wins[p]/n:>6.1f}% {pts_sum[p]/n:>8.1f} {gd_sum[p]/n:>8.1f}")

    # --- export for the web vis -------------------------------------------
    if json_path:
        prev = {}
        if history_path and os.path.exists(history_path):
            try:
                hist = json.load(open(history_path))
                if hist:
                    prev = hist[-1].get("w", {})
            except (ValueError, OSError):
                hist = []
        else:
            hist = []

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        gtables = actual_group_tables()
        players = []
        for p in rows:
            wp = round(100 * wins[p] / n, 1)
            players.append({
                "name": p,
                "teams": PLAYERS[p],
                "flags": [flag(t) for t in PLAYERS[p]],
                "teamMeta": [gtables.get(t) for t in PLAYERS[p]],
                "winPct": wp,
                "currentPts": actual_score[p]["pts"],
                "currentGd": actual_score[p]["gd"],
                "avgPts": round(pts_sum[p] / n, 1),
                "avgGd": round(gd_sum[p] / n, 1),
                "deltaPct": round(wp - prev.get(p, wp), 1),
            })
        payload = {
            "title": TITLE,
            "generatedAt": now,
            "nSims": n,
            "matchesPlayed": matches_played(),
            "players": players,
        }
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        json.dump(payload, open(json_path, "w"), indent=2)

        if history_path:
            hist.append({"t": now, "w": {p["name"]: p["winPct"] for p in players}})
            json.dump(hist[-60:], open(history_path, "w"))   # keep last 60 snapshots

if __name__ == "__main__":
    # In CI, set RESULTS_JSON / HISTORY_JSON to publish the web data.
    run(json_path=os.environ.get("RESULTS_JSON"),
        history_path=os.environ.get("HISTORY_JSON"))

# ===========================================================================
#  UPGRADE PATH — where real MCMC comes in
# ===========================================================================
# What you described ("MCMC to simulate outcomes") is really two ideas:
#
#   1. Monte Carlo simulation  -> run the tournament forward N times, tally
#      win frequencies. That's the loop above. This is what gives each player
#      a win probability.
#
#   2. MCMC (Markov chain Monte Carlo) -> a way to *fit* the team ratings.
#      Instead of plugging in fixed Elo, you put a hierarchical Poisson model
#      on historical results and sample the posterior over each team's
#      attack/defense with PyMC or Stan. Then in each tournament sim you draw
#      one posterior sample of ratings, so parameter uncertainty propagates
#      into the win %s (wider, more honest spreads).
#
#   Sketch (PyMC):
#       atk ~ Normal(0, sigma_atk)        # per team
#       dff ~ Normal(0, sigma_def)        # per team
#       log(lambda_home) = base + home + atk[h] - dff[a]
#       goals_home ~ Poisson(lambda_home)
#   Sample -> get e.g. 4000 posterior rating sets -> feed sim_tournament().
#
#   Cheaper refinements without full Bayes:
#     * Dixon-Coles low-score correction (DONE — see RHO / _dc_joint above).
#     * Time-decay weighting so recent form counts more.
#     * Bivariate Poisson for home/away goal correlation.

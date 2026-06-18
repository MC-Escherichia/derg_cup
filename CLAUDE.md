# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Monte Carlo pool simulator for the 2026 FIFA World Cup. It runs the remaining tournament thousands of times and publishes each player's win probability to a static GitHub Pages board. No backend; no API key required.

## Running locally

```bash
# Install the only dependency
pip install numpy

# Run with demo data (no pool.json needed)
python wc_pool_sim.py

# Run with real data and write the board's data file
RESULTS_JSON=docs/results.json HISTORY_JSON=docs/history.json python wc_pool_sim.py

# Pull latest group-stage scores into pool.json before simulating
python fetch_results.py
python fetch_results.py pool.json                     # explicit path
python fetch_results.py pool.json <custom_url>        # custom source
```

The board (`docs/index.html`) reads `docs/results.json` via `fetch()`, so open it via a local server (`python -m http.server` in `docs/`) rather than directly as a file.

## Architecture

### Data flow

```
pool.json + ratings.json
        │
        ▼
   load_config()          ← evolves Elo in-memory from played results
        │
        ▼
   Dixon-Coles Poisson model (wc_pool_sim.py)
        │
        ▼
   run() — N_SIMS Monte Carlo iterations
        │
        ▼
   docs/results.json  ←  docs/index.html reads this at page load
```

### Key files

| File | Role |
|---|---|
| `pool.json` | **The only file edited regularly.** Pool title, players (nickname → `[top16, mid16, bot16]`), the official group draw, played results. |
| `ratings.json` | Pre-tournament Elo snapshot, downloaded once. Never refresh mid-tournament — `EVOLVE_ELO = True` replays results from this baseline. |
| `wc_pool_sim.py` | All simulation logic: Elo evolution, Dixon-Coles goal model, group stage, bracket, Monte Carlo loop, JSON export. |
| `fetch_results.py` | Pulls group-stage scores from `openfootball/worldcup.json` into `pool.json`. Knockout results are entered manually in `koResults`. |
| `docs/index.html` | Standalone board (vanilla JS, no framework). Reads `results.json` and `history.json` from the same directory. |
| `sim.yml` | GitHub Actions workflow: fetch → simulate → deploy to Pages. Runs every 3 hours and on push. |

### Simulation internals

- **Goal model:** Dixon-Coles corrected bivariate Poisson — `_dc_joint()` reweights the 2×2 low-score corner via `RHO`.
- **Ratings:** Elo → symmetric `{atk, def}` offsets via `elo_to_ratings()`. Evolved each run through played matches with `_wfe_update()`.
- **Bracket:** matches 73–104 per `KO_GRAPH`. Third-place slots use `assign_thirds()` (Kuhn's bipartite matching on `THIRD_SLOTS`) or `thirdPlaceOverride` once FIFA publishes the bracket.
- **Scoring:** `award()` routes points/GD to the owning pool player via `TEAM_OWNER`.
- **Cache:** `_dc_cumdist` is `@lru_cache`'d per matchup pair — clear it with `_dc_cumdist.cache_clear()` if you change `RHO` or ratings at runtime.

### Tunable constants (top of `wc_pool_sim.py`)

| Constant | Default | Effect |
|---|---|---|
| `N_SIMS` | 20,000 | Monte Carlo iterations |
| `ELO_SCALE` | 450 | Passed to `elo_to_ratings()`; lower = stronger favorites |
| `RHO` | −0.08 | Dixon-Coles low-score correction |
| `PEN_WIN_POINTS` | 3 | Pool points for winning a shootout |
| `EVOLVE_ELO` | `True` | Set `False` before pasting a mid-tournament Elo dump |

## Updating the pool during the tournament

- **Group results** — auto-fetched by `fetch_results.py` (CI does this every 3 hours).
- **Knockout results** — add to `koResults` in `pool.json` by match number (73–104). Goals are regulation + ET; include a 5th element (winning team name) for shootouts.
- **R32 bracket** — once FIFA publishes it, populate `thirdPlaceOverride` with `{winner_group: third_place_group, ...}` to pin real slots instead of simulating placement.

## Team name matching

Team names must be consistent across `groups`, `players`, and `ratings.json` (case-insensitive). Common aliases like `"Korea Republic"` → `"South Korea"` are handled by `_ISO` tables in both scripts. Unknown names produce a warning (not a crash); add the alias to `_ISO` in `wc_pool_sim.py` to get a flag emoji, and to `_ISO` in `fetch_results.py` to get auto-fetch matching.

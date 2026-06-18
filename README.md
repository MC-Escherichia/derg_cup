# World Cup Pool — live odds board

A Monte Carlo simulator for a World Cup pool that publishes a phone-friendly
odds board to GitHub Pages. It runs the rest of the tournament thousands of
times and shows each player's chance of winning, updating as results come in.

- **Model:** Dixon-Coles Poisson goals, team strength from eloratings.net
  (calibrated so win probabilities match Elo's own formula).
- **Bracket:** the real 2026 format — 48 teams, best-8 third-placed teams, and
  FIFA's Annex C third-place placement, simulated before the Round of 32 is set.
- **Board:** ranked win %, momentum (▲/▼ since the last update), per-player
  sparklines, and country flags — all on a single static page, no backend.

## Files

| File | What it is |
|---|---|
| `pool.json` | **The only file you edit regularly.** Title, players, the group draw, and results. |
| `ratings.json` | Pre-tournament Elo, downloaded **once**. The sim evolves it from results automatically (eloratings.net formula), so you don't refresh it. |
| `fetch_results.py` | Pulls live group-stage scores into `pool.json` from `openfootball/worldcup.json` (no API key). |
| `wc_pool_sim.py` | The simulator. Knobs at the top; you rarely touch this. |
| `docs/index.html` | The board (vanilla JS, single file, no dependencies). |
| `.github/workflows/sim.yml` | Fetches scores, runs the sim, and deploys the board — on a schedule and on every push. |

## Setup (one time)

1. **Create a new GitHub repository** (public — see *Privacy* below).
2. **Add the files** at these exact paths:
   ```
   wc_pool_sim.py
   fetch_results.py
   pool.json
   ratings.json
   docs/index.html
   .github/workflows/sim.yml
   ```
3. **Fill in `pool.json`:** your pool `title`, each player's nickname → three
   teams `[top16, mid16, bot16]`, the official group draw, and any results so
   far. Put current Elo in `ratings.json`. (The samples already run, so you can
   skip this to try it first.)
4. **Commit and push to `main`.**
5. In the repo, go to **Settings → Pages → Build and deployment** and set
   **Source: GitHub Actions**.
6. Open the **Actions** tab. The `pool-sim` workflow runs on every push, or you
   can click **Run workflow**. When it finishes, your board is live at:
   ```
   https://<your-username>.github.io/<your-repo>/
   ```
7. **Share that link** in the group chat.

Before the first run finishes, the page shows a labelled "Preview" so it never
looks broken.

## Running it

Once it's set up, it runs itself. The workflow fires **every 3 hours** (and on
any push), and each run:

1. pulls the latest group-stage scores into `pool.json` (`fetch_results.py`),
2. evolves the Elo from those scores (no rating downloads needed),
3. re-simulates the rest of the tournament, and
4. redeploys the board.

So during the group stage you don't have to touch anything — the board keeps
itself current. You only commit a change for:

- **Knockout results** — add them to `koResults` in `pool.json` (auto-fetch
  covers the group stage only; knockout games map to bracket slots by hand).
- **The R32 bracket** — once it's published, set `thirdPlaceOverride` so the
  simulated bracket matches the official one.
- **Roster or title edits.**

To force an immediate refresh, push or click **Run workflow** in the Actions tab.

## Editing `pool.json`

```jsonc
{
  "title": "The Group Chat Cup",
  "players": {                         // nickname -> [top16, mid16, bot16]
    "Matt": ["Spain", "Mexico", "Cape Verde"]
  },
  "groups": {                          // the official draw, A-L
    "A": ["Mexico", "South Africa", "South Korea", "Czechia"]
  },
  "results": [                         // group games that have been played
    {"group": "A", "home": "Mexico", "away": "South Africa", "score": [2, 0]}
  ],
  "thirdPlaceOverride": {},            // once the R32 bracket is published, pin
                                       //   real slots: {"E":"C","I":"H", ...}
  "koResults": {                       // knockout games already played
    "77": ["Spain", 2, "Germany", 1],          // decided in regulation/ET
    "74": ["France", 1, "Brazil", 1, "France"] // level -> shootout winner last
  }
}
```

Spell each team the same way across `groups`, `players`, and `ratings.json`
(matching is case-insensitive, with aliases like "Korea Republic" → "South
Korea"). Flags are derived from the name automatically; an unknown name shows no
flag until you add it to the `_ISO` table in `wc_pool_sim.py`.

## Data sources

- **Results** are pulled automatically by `fetch_results.py` from
  `openfootball/worldcup.json` (public domain, no key). Teams are matched by
  country identity, so spelling differences ("Czech Republic" vs "Czechia")
  still line up; unmatched names are reported, not guessed. If the source is
  briefly unreachable, your committed `pool.json` is used unchanged. If you'd
  rather not auto-fetch, delete the *Fetch latest results* step from the
  workflow and enter scores by hand.
- **Elo** is downloaded once (pre-tournament) into `ratings.json` and then
  evolved by the sim using eloratings.net's own formula (`K=60`, goal-difference
  multiplier, shootouts counted as draws). Don't paste a mid-tournament Elo dump
  over it, or you'd double-count — set `EVOLVE_ELO = False` first if you ever do.

## Tuning (in `wc_pool_sim.py`)

| Setting | Does |
|---|---|
| `N_SIMS` | Simulations per run (default 20,000). |
| `ELO_SCALE` | Lower = punchier favorites / bigger swings (default 450, Elo-calibrated). |
| `RHO` | Dixon-Coles low-score correction (default −0.08). |
| `PEN_WIN_POINTS` | Points for winning a shootout — your call (3 or 1). |

## Privacy

A public repo means a public Pages URL (anyone with the link can see it). It's
only win percentages, but use handles instead of real names if you'd rather not
be searchable. Pages on a private repo requires a paid GitHub plan.

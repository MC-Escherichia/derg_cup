#!/usr/bin/env python3
"""
Backfill history.json by simulating the tournament at each match checkpoint.
Shows probability evolution from start to current state.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

def load_pool():
    with open('pool.json') as f:
        return json.load(f)

def simulate_at_checkpoint(num_results):
    """Modify pool.json, run simulation, capture results, restore pool.json."""
    pool = load_pool()
    original_results = pool['results'].copy()

    # Set results to checkpoint
    pool['results'] = original_results[:num_results]

    # Write temp pool
    with open('pool.json', 'w') as f:
        json.dump(pool, f, indent=2)

    try:
        # Run the simulation with JSON output
        import subprocess
        result = subprocess.run(
            ['python3', '-c', '''
import sys
sys.path.insert(0, ".")
from wc_pool_sim import run
run(json_path="docs/results.json")
'''],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            print(f"  Error running simulation: {result.stderr[:100]}")
            return None

        # Read the results.json that was just created
        with open('docs/results.json') as f:
            results = json.load(f)

        win_pcts = {p['name']: p['winPct'] for p in results['players']}
        return win_pcts

    finally:
        # Restore original pool.json
        pool['results'] = original_results
        with open('pool.json', 'w') as f:
            json.dump(pool, f, indent=2)

def backfill():
    pool = load_pool()
    results = pool.get('results', [])

    if not results:
        print("No results to backfill from.")
        return

    print(f"Backfilling history from {len(results)} matches...\n")

    history = []
    base_time = datetime.now(timezone.utc) - timedelta(hours=len(results) * 3)

    # Snapshot 0: Pre-tournament (0 results)
    print(f"[0/{len(results)}] Pre-tournament state...", end='', flush=True)
    wp = simulate_at_checkpoint(0)
    if wp:
        t = base_time.isoformat(timespec='seconds')
        history.append({"t": t, "w": wp})
        print(f" ✓ ({len(wp)} players)")
    else:
        print(" ✗")

    # Snapshots after each match
    for i in range(1, len(results) + 1):
        match = results[i-1]
        print(f"[{i}/{len(results)}] {match['home']} {match['score'][0]}-{match['score'][1]} {match['away']}...",
              end='', flush=True)
        wp = simulate_at_checkpoint(i)
        if wp:
            t = (base_time + timedelta(hours=3*i)).isoformat(timespec='seconds')
            history.append({"t": t, "w": wp})
            print(f" ✓")
        else:
            print(" ✗")

    # Save history
    with open('docs/history.json', 'w') as f:
        json.dump(history, f, indent=2)

    print(f"\n✅ Generated {len(history)} snapshots")
    print(f"📊 Saved to docs/history.json")

if __name__ == '__main__':
    backfill()

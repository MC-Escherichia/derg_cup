{
  description = "World Cup Pool — Monte Carlo simulator and live odds board";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python312;
        pythonEnv = python.withPackages (ps: [ ps.numpy ]);

        # Wrapper scripts so `nix run .#fetch` and `nix run .#sim` work
        fetchScript = pkgs.writeShellApplication {
          name = "derg-fetch";
          runtimeInputs = [ pythonEnv ];
          text = ''
            cd "''${DERG_DIR:-$PWD}"
            python ${./fetch_results.py} "''${1:-pool.json}"
          '';
        };

        simScript = pkgs.writeShellApplication {
          name = "derg-sim";
          runtimeInputs = [ pythonEnv ];
          text = ''
            cd "''${DERG_DIR:-$PWD}"
            RESULTS_JSON="''${RESULTS_JSON:-docs/results.json}" \
            HISTORY_JSON="''${HISTORY_JSON:-docs/history.json}" \
            python ${./wc_pool_sim.py}
          '';
        };

        runScript = pkgs.writeShellApplication {
          name = "derg-run";
          runtimeInputs = [ pythonEnv pkgs.curl ];
          text = ''
            set -euo pipefail
            DIR="''${DERG_DIR:-$PWD}"
            cd "$DIR"

            python ${./fetch_results.py} pool.json

            mkdir -p docs
            # Best-effort history rehydration (mirrors the GitHub Actions step)
            HISTORY="$DIR/docs/history.json"
            if [ -n "''${PAGES_URL:-}" ]; then
              curl -fsSL "$PAGES_URL/history.json" -o "$HISTORY" 2>/dev/null \
                || echo "[]" > "$HISTORY"
            elif [ ! -f "$HISTORY" ]; then
              echo "[]" > "$HISTORY"
            fi

            RESULTS_JSON="$DIR/docs/results.json" \
            HISTORY_JSON="$HISTORY" \
            python ${./wc_pool_sim.py}
          '';
        };
      in
      {
        packages = {
          fetch = fetchScript;
          sim   = simScript;
          run   = runScript;
          default = runScript;
        };

        apps = {
          fetch   = { type = "app"; program = "${fetchScript}/bin/derg-fetch"; };
          sim     = { type = "app"; program = "${simScript}/bin/derg-sim"; };
          run     = { type = "app"; program = "${runScript}/bin/derg-run"; };
          default = { type = "app"; program = "${runScript}/bin/derg-run"; };
        };

        devShells.default = pkgs.mkShell {
          packages = [ pythonEnv pkgs.curl ];
          shellHook = ''
            echo "derg-cup dev shell  (Python $(python --version))"
            echo "  nix run .#fetch   — pull latest group-stage scores"
            echo "  nix run .#sim     — run the Monte Carlo simulation"
            echo "  nix run           — fetch + sim (full pipeline)"
          '';
        };
      }
    )

    //

    # NixOS module — import this from your system flake to get a systemd timer
    {
      nixosModules.derg-cup = { config, lib, pkgs, ... }:
        let
          cfg = config.services.derg-cup;
          inherit (lib) mkEnableOption mkOption types mkIf;

          python = pkgs.python312;
          pythonEnv = python.withPackages (ps: [ ps.numpy ]);
        in
        {
          options.services.derg-cup = {
            enable = mkEnableOption "World Cup pool simulator service";

            dataDir = mkOption {
              type    = types.path;
              default = "/var/lib/derg-cup";
              description = "Directory containing pool.json, ratings.json, and docs/.";
            };

            pagesUrl = mkOption {
              type    = types.str;
              default = "";
              description = "Base URL of the deployed GitHub Pages site, used to rehydrate history.json (optional).";
            };

            interval = mkOption {
              type    = types.str;
              default = "3h";
              description = "Systemd calendar interval for the timer (e.g. '3h', 'hourly', '*:0/30').";
            };

            user  = mkOption { type = types.str; default = "derg-cup"; };
            group = mkOption { type = types.str; default = "derg-cup"; };
          };

          config = mkIf cfg.enable {
            users.users.${cfg.user} = {
              isSystemUser = true;
              group        = cfg.group;
              home         = cfg.dataDir;
              createHome   = true;
            };
            users.groups.${cfg.group} = {};

            systemd.services.derg-cup = {
              description   = "World Cup pool — fetch scores and run simulation";
              after         = [ "network-online.target" ];
              wants         = [ "network-online.target" ];
              serviceConfig = {
                Type             = "oneshot";
                User             = cfg.user;
                Group            = cfg.group;
                WorkingDirectory = cfg.dataDir;
                Environment      = [
                  "RESULTS_JSON=${cfg.dataDir}/docs/results.json"
                  "HISTORY_JSON=${cfg.dataDir}/docs/history.json"
                ] ++ lib.optional (cfg.pagesUrl != "")
                    "PAGES_URL=${cfg.pagesUrl}";
                ExecStart = pkgs.writeShellScript "derg-cup-run" ''
                  set -euo pipefail
                  mkdir -p ${cfg.dataDir}/docs

                  ${pythonEnv}/bin/python ${self}/fetch_results.py pool.json

                  HISTORY="${cfg.dataDir}/docs/history.json"
                  if [ -n "''${PAGES_URL:-}" ]; then
                    ${pkgs.curl}/bin/curl -fsSL "$PAGES_URL/history.json" \
                      -o "$HISTORY" 2>/dev/null || echo "[]" > "$HISTORY"
                  elif [ ! -f "$HISTORY" ]; then
                    echo "[]" > "$HISTORY"
                  fi

                  ${pythonEnv}/bin/python ${self}/wc_pool_sim.py
                '';
              };
            };

            systemd.timers.derg-cup = {
              description      = "World Cup pool simulator timer";
              wantedBy         = [ "timers.target" ];
              timerConfig = {
                OnBootSec     = "2min";
                OnUnitActiveSec = cfg.interval;
                Persistent    = true;
              };
            };
          };
        };
    };
}

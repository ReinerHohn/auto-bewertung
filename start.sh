#!/usr/bin/env bash
#
# Ein-Klick-Setup + Start fuer das Auto-Bewertungstool.
#
#   ./start.sh                 # einrichten, Daten sammeln, Dashboard starten
#   ./start.sh --no-dashboard  # nur einrichten + Ranking in der Konsole
#   ./start.sh --rank          # nur Ranking ausgeben (setzt Einrichtung voraus)
#   ./start.sh --inserate-csv meine_liste.csv   # zusaetzlich Angebote importieren
#
# Idempotent: legt beim ersten Lauf ein venv an und installiert Abhaengigkeiten,
# danach werden diese Schritte uebersprungen.
set -euo pipefail

cd "$(dirname "$0")"
VENV=".venv"
PY="python3"
DASHBOARD=1
RANK_ONLY=0
CSV_ARG=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-dashboard) DASHBOARD=0; shift ;;
    --rank)         RANK_ONLY=1; DASHBOARD=0; shift ;;
    --inserate-csv) CSV_ARG=(--inserate-csv "$2"); shift 2 ;;
    -h|--help)      grep '^#' "$0" | sed 's/^#//'; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 1 ;;
  esac
done

info() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }

# --- 1. venv anlegen --------------------------------------------------------
if [[ ! -d "$VENV" ]]; then
  info "Lege virtuelle Umgebung an ($VENV) ..."
  if ! "$PY" -m venv "$VENV" 2>/dev/null; then
    echo "FEHLER: 'python3 -m venv' schlug fehl." >&2
    echo "Installiere das venv-Paket, z.B.:  sudo apt install python3-venv" >&2
    exit 1
  fi
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# --- 2. Abhaengigkeiten installieren (nur wenn noetig) ----------------------
STAMP="$VENV/.deps-installed"
if [[ ! -f "$STAMP" || requirements.txt -nt "$STAMP" ]]; then
  info "Installiere Abhaengigkeiten ..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  touch "$STAMP"
else
  info "Abhaengigkeiten bereits installiert."
fi

# --- 3. Nur Ranking? --------------------------------------------------------
if [[ "$RANK_ONLY" == 1 ]]; then
  python -m autobewertung.collect rank
  exit 0
fi

# --- 4. Daten sammeln (DB wird dabei angelegt) ------------------------------
info "Sammle Daten in die Datenbank ..."
python -m autobewertung.collect run "${CSV_ARG[@]}"

echo
info "Aktuelles Ranking:"
python -m autobewertung.collect rank --top 10

# --- 5. Dashboard starten ---------------------------------------------------
if [[ "$DASHBOARD" == 1 ]]; then
  echo
  info "Starte Dashboard auf http://localhost:8501  (Strg+C zum Beenden)"
  exec streamlit run autobewertung/dashboard.py
fi

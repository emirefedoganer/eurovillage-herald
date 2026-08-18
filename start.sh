#!/bin/bash
# Production start script for Railway (or any host giving one persistent
# volume). The app's code (app.py/store.py) is untouched -- this script
# redirects its existing hardcoded data/upload directories onto the
# persistent volume via symlinks, created once at container boot.
set -e

PERSIST_ROOT="${PERSIST_DIR:-/data}"
APP_DIR="$(cd "$(dirname "$0")/app" && pwd)"

seed_if_missing() {
  local persist_path="$1" source_path="$2"
  if [ ! -d "$persist_path" ]; then
    mkdir -p "$persist_path"
    if [ -d "$source_path" ]; then
      cp -r "$source_path/." "$persist_path/" 2>/dev/null || true
    fi
  fi
}

seed_if_missing "$PERSIST_ROOT/data" "$APP_DIR/data"
seed_if_missing "$PERSIST_ROOT/img_articles" "$APP_DIR/static/img/articles"
seed_if_missing "$PERSIST_ROOT/img_authors" "$APP_DIR/static/img/authors"
seed_if_missing "$PERSIST_ROOT/issues" "$APP_DIR/static/issues"

link() {
  rm -rf "$2"
  ln -s "$1" "$2"
}

link "$PERSIST_ROOT/data" "$APP_DIR/data"
link "$PERSIST_ROOT/img_articles" "$APP_DIR/static/img/articles"
link "$PERSIST_ROOT/img_authors" "$APP_DIR/static/img/authors"
link "$PERSIST_ROOT/issues" "$APP_DIR/static/issues"

cd "$APP_DIR"
exec gunicorn app:app --bind 0.0.0.0:"${PORT:-8000}" --workers 2 --threads 4 --timeout 60

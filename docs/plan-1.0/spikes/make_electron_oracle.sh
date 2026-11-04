#!/bin/sh
# Build the bare Electron oracle used by the Electron claims.
# Usage: make_electron_oracle.sh <dir of any Electron app's runtime>
#   e.g. make_electron_oracle.sh /srv/lab/tools/obsidian/obsidian-1.14.4
# The runtime is symlinked; the app binary is copied under a neutral name and
# resources/app is replaced by targets.py's tiny main.js (written at run time).
set -eu
SRC=${1:?usage: make_electron_oracle.sh <electron app dir>}
DST="$HOME/.hermes/cache/scratch/electron-oracle"
BIN=$(find "$SRC" -maxdepth 1 -type f -perm -u+x ! -name '*.so*' ! -name 'chrome*' ! -name '*-cli' | head -1)
[ -n "$BIN" ] || { echo "no Electron binary found in $SRC" >&2; exit 1; }
rm -rf -- "$DST"
mkdir -p "$DST/resources/app"
for f in "$SRC"/*; do
  name=$(basename "$f")
  [ "$name" = resources ] && continue
  [ "$f" = "$BIN" ] && continue
  ln -s "$f" "$DST/$name"
done
cp "$BIN" "$DST/electron-oracle"
echo "$DST/electron-oracle"

#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/install-skill.sh [--with-claude]

Installs the shared-plan-storage skill for Pi and Codex. The installed client
selects a separate plan hub via --root PATH|NAME or the roots registry at
~/.config/plans-hub/roots.json.
EOF
}

install_claude=false
case "${1:-}" in
  "") ;;
  --with-claude) install_claude=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
client_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
source_dir="$client_root/skills/shared-plan-storage"

if [ ! -f "$source_dir/SKILL.md" ] || [ ! -x "$source_dir/bin/planctl" ]; then
  echo "install-skill: canonical skill is incomplete: $source_dir" >&2
  exit 1
fi

canonical_directory() {
  (CDPATH= cd -- "$1" 2>/dev/null && pwd -P)
}

resolved_link_directory() {
  link=$1
  target=$2
  case "$target" in
    /*) canonical_directory "$target" ;;
    *) canonical_directory "$(dirname -- "$link")/$target" ;;
  esac
}

# Print the single registered hub path from the fixed roots registry. Fail
# when the registry is missing, unreadable, or names zero or multiple roots:
# auto-detection never guesses a hub.
registry_single_root() {
  registry="$HOME/.config/plans-hub/roots.json"
  [ -f "$registry" ] || return 1
  python3 - "$registry" <<'EOF'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        roots = json.load(handle)["roots"]
    path = roots[0]["path"] if len(roots) == 1 else None
except (OSError, ValueError, KeyError, IndexError, TypeError):
    path = None
if not isinstance(path, str) or not path:
    sys.exit(1)
print(path)
EOF
}

is_known_legacy_link() {
  destination=$1
  current=$2
  hub_path=$(registry_single_root) || return 1
  hub_root=$(canonical_directory "$hub_path") || return 1
  legacy_source="$hub_root/skills/shared-plan-storage"
  [ "$current" = "$legacy_source" ] && return 0

  current_source=$(resolved_link_directory "$destination" "$current") || return 1
  [ "$current_source" = "$legacy_source" ]
}

install_link() {
  destination=$1
  mkdir -p "$(dirname -- "$destination")"
  if [ -L "$destination" ]; then
    current=$(readlink "$destination")
    if [ "$current" = "$source_dir" ]; then
      echo "Already installed: $destination -> $source_dir"
    elif is_known_legacy_link "$destination" "$current"; then
      rm -- "$destination"
      ln -s "$source_dir" "$destination"
      echo "Upgraded legacy installation: $destination -> $source_dir"
    else
      echo "install-skill: refusing to replace existing symlink: $destination -> $current" >&2
      exit 1
    fi
  elif [ -e "$destination" ]; then
    echo "install-skill: refusing to replace existing path: $destination" >&2
    exit 1
  else
    ln -s "$source_dir" "$destination"
    echo "Installed: $destination -> $source_dir"
  fi
}

install_link "$HOME/.agents/skills/shared-plan-storage"
if [ "$install_claude" = true ]; then
  install_link "$HOME/.claude/skills/shared-plan-storage"
fi

if hub_path=$(registry_single_root); then
  "$source_dir/bin/planctl" --root "$hub_path" validate
else
  echo "No hub validated: pass --root PATH|NAME or register exactly one hub in ~/.config/plans-hub/roots.json."
fi

echo "Skill installation complete. Restart active agent sessions if needed."

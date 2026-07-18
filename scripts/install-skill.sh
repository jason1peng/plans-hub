#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/install-skill.sh [--with-claude]

Installs the shared-plan-storage skill for Pi and Codex. The installed client
still requires --root PATH or PLANS_ROOT to select a separate plan hub.
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

is_known_legacy_link() {
  destination=$1
  current=$2
  [ -n "${PLANS_ROOT:-}" ] || return 1

  hub_root=$(canonical_directory "$PLANS_ROOT") || return 1
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

if [ -n "${PLANS_ROOT:-}" ]; then
  "$source_dir/bin/planctl" validate
else
  echo "No hub validated: set PLANS_ROOT or pass --root when invoking planctl."
fi

echo "Skill installation complete. Restart active agent sessions if needed."

#!/usr/bin/env sh
set -eu

REPO_URL="https://github.com/flywithbug/tools.git"
PKG_NAME="fb-box"

echo "== box installer =="

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

if ! need_cmd curl; then
  echo "error: curl not found"
  exit 1
fi

if ! need_cmd python3; then
  echo "error: python3 not found"
  exit 1
fi

# Install pipx if missing
if ! need_cmd pipx; then
  echo "pipx not found, installing pipx (user)..."
  python3 -m pip install --user -U pipx
  python3 -m pipx ensurepath || true
fi

# PATH hint (common)
USER_BIN="$HOME/.local/bin"
case ":$PATH:" in
  *":$USER_BIN:"*) ;;
  *)
    if [ -d "$USER_BIN" ]; then
      echo "note: $USER_BIN not in PATH. You may need to add it to your shell profile."
    fi
    ;;
esac

echo "installing box from: $REPO_URL"
pipx install --force "git+$REPO_URL"

echo ""
echo "OK. Try:"
echo "  box --help"
echo "  box doctor"
echo "  box update"

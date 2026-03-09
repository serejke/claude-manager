#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
DIM="\033[2m"
RESET="\033[0m"

echo -e "${BOLD}Claude Manager — installer${RESET}\n"

# ── Check prerequisites ─────────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    echo -e "${YELLOW}uv not found.${RESET} Install it first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ── Ask for Claude binary name ──────────────────────────────────────────────

DEFAULT_BINARY="claude"
echo -e "Which Claude Code binary do you use?"
echo -e "  ${DIM}Most people use 'claude'. If you have a custom wrapper/alias, enter its name.${RESET}"
read -rp "Binary name [${DEFAULT_BINARY}]: " BINARY
BINARY="${BINARY:-$DEFAULT_BINARY}"

# ── Install the package ─────────────────────────────────────────────────────

echo -e "\n${BOLD}Installing claude-manager via uv...${RESET}"
uv tool install --force "claude-manager@${CLAUDE_MANAGER_SOURCE:-git+https://github.com/serejke/claude-manager}"

# ── Configure shell ─────────────────────────────────────────────────────────

SHELL_NAME="$(basename "$SHELL")"
case "$SHELL_NAME" in
    zsh)  RC_FILE="$HOME/.zshrc" ;;
    bash) RC_FILE="$HOME/.bashrc" ;;
    *)    RC_FILE="" ;;
esac

ENV_LINE="export CLAUDE_BINARY=\"${BINARY}\""
ALREADY_SET=false

if [[ -n "$RC_FILE" ]]; then
    # Remove any old CLAUDE_BINARY export
    if grep -q 'export CLAUDE_BINARY=' "$RC_FILE" 2>/dev/null; then
        sed -i.bak '/export CLAUDE_BINARY=/d' "$RC_FILE"
        ALREADY_SET=true
    fi

    # Remove old alias-style claude-manager lines
    if grep -q 'alias claude-manager=' "$RC_FILE" 2>/dev/null; then
        sed -i.bak '/alias claude-manager=/d' "$RC_FILE"
    fi

    # Add env var if binary is not the default
    if [[ "$BINARY" != "claude" ]]; then
        echo "" >> "$RC_FILE"
        echo "# Claude Manager — claude binary override" >> "$RC_FILE"
        echo "$ENV_LINE" >> "$RC_FILE"
        echo -e "${GREEN}Added${RESET} ${DIM}${ENV_LINE}${RESET} to ${RC_FILE}"
    fi
fi

# ── Verify ──────────────────────────────────────────────────────────────────

echo ""
if command -v claude-manager &>/dev/null; then
    echo -e "${GREEN}Installed successfully.${RESET}"
else
    echo -e "${GREEN}Installed.${RESET} You may need to restart your shell or run:"
    echo -e "  ${DIM}source ${RC_FILE}${RESET}"
fi

echo -e "\n${BOLD}Usage:${RESET}"
echo "  claude-manager          # pick from 20 recent sessions"
echo "  claude-manager 50       # pick from 50 recent sessions"
echo "  claude-manager -b my-claude  # override binary for this run"
echo ""

#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; DIM="\033[2m"; RESET="\033[0m"

if ! command -v uv &>/dev/null; then
    echo -e "${YELLOW}uv not found.${RESET} curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

read -rp "Claude binary [claude]: " BINARY
BINARY="${BINARY:-claude}"

echo -e "${BOLD}Installing polyclaude...${RESET}"
uv tool install --force "polyclaude@${POLYCLAUDE_SOURCE:-git+https://github.com/serejke/polyclaude}"

if [[ "$BINARY" != "claude" ]]; then
    case "$(basename "$SHELL")" in
        zsh)  RC="$HOME/.zshrc" ;;
        bash) RC="$HOME/.bashrc" ;;
        *)    RC="" ;;
    esac
    if [[ -n "$RC" ]]; then
        sed -i.bak '/export CLAUDE_BINARY=/d;/alias claude-manager=/d' "$RC" 2>/dev/null || true
        echo "export CLAUDE_BINARY=\"${BINARY}\"" >> "$RC"
        echo -e "${GREEN}+${RESET} ${DIM}CLAUDE_BINARY=${BINARY} → ${RC}${RESET}"
    fi
fi

echo -e "\n${GREEN}Done.${RESET} Run: ${BOLD}polyclaude${RESET}"

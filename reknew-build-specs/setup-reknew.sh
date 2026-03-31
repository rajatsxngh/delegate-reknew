#!/bin/bash
# setup-reknew.sh — Run this after forking Delegate to set up the ReKnew layer
# Usage: bash setup-reknew.sh

set -e

echo "═══════════════════════════════════════════"
echo "  ReKnew AI-SDLC Platform — Setup Script"
echo "═══════════════════════════════════════════"
echo ""

# Check prerequisites
echo "[1/6] Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3.12+ required. Install: brew install python@3.13"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  ✓ Python $PYTHON_VERSION"

if ! command -v git &> /dev/null; then
    echo "  ✗ Git required. Install: brew install git"
    exit 1
fi
echo "  ✓ Git $(git --version | cut -d' ' -f3)"

if ! command -v claude &> /dev/null; then
    echo "  ⚠ Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code"
    echo "    (Required for Phase 1, but setup can continue)"
fi

if ! command -v openspec &> /dev/null; then
    echo "  ⚠ OpenSpec CLI not found. Install: npx openspec init"
    echo "    (Required for spec generation, but setup can continue)"
fi

# Check env vars
if [ -z "$GITHUB_TOKEN" ]; then
    echo "  ⚠ GITHUB_TOKEN not set. Required for GitHub API."
    echo "    Set with: export GITHUB_TOKEN=ghp_your_token"
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "  ⚠ ANTHROPIC_API_KEY not set. Required for Claude Code."
    echo "    Set with: export ANTHROPIC_API_KEY=sk-ant-your_key"
fi

# Create ReKnew directory structure
echo ""
echo "[2/6] Creating ReKnew directory structure..."

mkdir -p reknew/{connectors,spec,orchestration,adapters,reactions,github,dashboard/frontend/src/{components,hooks,utils}}
mkdir -p tests
mkdir -p specs

# Create __init__.py files
for dir in reknew reknew/connectors reknew/spec reknew/orchestration reknew/adapters reknew/reactions reknew/github reknew/dashboard; do
    touch "$dir/__init__.py"
    echo "  ✓ $dir/__init__.py"
done

# Copy spec files
echo ""
echo "[3/6] Copying spec files..."

if [ -d "../reknew-build-specs/specs" ]; then
    cp ../reknew-build-specs/specs/*.md specs/
    echo "  ✓ Spec files copied to specs/"
elif [ -d "specs" ] && [ -f "specs/phase1-issue-connector.md" ]; then
    echo "  ✓ Spec files already in place"
else
    echo "  ⚠ Spec files not found. Copy them manually to specs/"
fi

# Copy config files
echo ""
echo "[4/6] Setting up config files..."

if [ ! -f "reknew.yaml" ]; then
    if [ -f "../reknew-build-specs/reknew.yaml" ]; then
        cp ../reknew-build-specs/reknew.yaml .
    fi
    echo "  ✓ reknew.yaml created"
else
    echo "  ✓ reknew.yaml already exists"
fi

if [ ! -f "CLAUDE.md" ]; then
    if [ -f "../reknew-build-specs/CLAUDE.md" ]; then
        cp ../reknew-build-specs/CLAUDE.md .
    fi
    echo "  ✓ CLAUDE.md created"
fi

if [ ! -f "AGENTS.md" ]; then
    if [ -f "../reknew-build-specs/AGENTS.md" ]; then
        cp ../reknew-build-specs/AGENTS.md .
    fi
    echo "  ✓ AGENTS.md created"
fi

# Set up upstream remote
echo ""
echo "[5/6] Configuring git remotes..."

if git remote | grep -q upstream; then
    echo "  ✓ upstream remote already configured"
else
    git remote add upstream https://github.com/nikhilgarg28/delegate.git
    echo "  ✓ upstream remote added (nikhilgarg28/delegate)"
fi

# Create ReKnew directories
echo ""
echo "[6/6] Creating workspace directories..."

mkdir -p ~/.reknew/{repos,worktrees,logs}
echo "  ✓ ~/.reknew/repos/"
echo "  ✓ ~/.reknew/worktrees/"
echo "  ✓ ~/.reknew/logs/"

echo ""
echo "═══════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit reknew.yaml with your repos"
echo "  2. Export GITHUB_TOKEN and ANTHROPIC_API_KEY"
echo "  3. Run: uv sync"
echo "  4. Start building Phase 1:"
echo "     Read specs/phase1-issue-connector.md"
echo "     Build: reknew/config.py"
echo "     Then:  reknew/connectors/github_connector.py"
echo "     Then:  reknew/spec/openspec_bridge.py"
echo "     Then:  reknew/main.py"
echo "     Test:  python -m reknew.main owner/repo 114"
echo "═══════════════════════════════════════════"

#!/usr/bin/env bash
# install-git-hook.sh — Idempotent, worktree-safe git hook installer for ProjectHub.
#
# Requires bash: uses `${REPO_PATH/#\~/$HOME}` parameter substitution for safe,
# eval-free tilde expansion (a POSIX `sh` does not support this form).
#
# Usage:
#   ./scripts/install-git-hook.sh <repo-path> <board-key> [backend-url] [secret] [--hooks-dir <path>]
#
# Arguments:
#   repo-path    Local path to the git repository (e.g. ~/Documents/project-hub)
#   board-key    ProjectHub board key (e.g. PH)
#   backend-url  ProjectHub backend URL (default: http://localhost:8000)
#   secret       refresh_secret value from connect_repository --json output
#
# Options:
#   --hooks-dir <path>  Override hooks directory (default: resolved via git-common-dir)
#
# Idempotency:
#   - First install: appends a BEGIN/END marked block to each hook file.
#   - Re-run with same args: detects marker + identical content → "already installed (matched)", no change.
#   - Re-run with changed secret/url: replaces the block in-place, preserves surrounding content.
#
# Worktree-safe:
#   Uses `git rev-parse --git-common-dir` to find the shared hooks directory.
#   All worktrees share a single hooks set under the main repo's .git/.
#
# Exit codes:
#   0  success (installed, updated, or already up-to-date)
#   1  usage error or unsupported repo type (bare)
#   2  git not found

set -e

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

REPO_PATH=""
BOARD_KEY=""
BACKEND_URL=""
SECRET=""
HOOKS_DIR_OVERRIDE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --hooks-dir)
            HOOKS_DIR_OVERRIDE="$2"
            shift 2
            ;;
        --hooks-dir=*)
            HOOKS_DIR_OVERRIDE="${1#--hooks-dir=}"
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            if [ -z "$REPO_PATH" ]; then
                REPO_PATH="$1"
            elif [ -z "$BOARD_KEY" ]; then
                BOARD_KEY="$1"
            elif [ -z "$BACKEND_URL" ]; then
                BACKEND_URL="$1"
            elif [ -z "$SECRET" ]; then
                SECRET="$1"
            fi
            shift
            ;;
    esac
done

if [ -z "$REPO_PATH" ] || [ -z "$BOARD_KEY" ]; then
    echo "Usage: $0 <repo-path> <board-key> [backend-url] [secret] [--hooks-dir <path>]" >&2
    exit 1
fi

# Apply defaults after parsing so positional arg 3 is correctly captured
: "${BACKEND_URL:=http://localhost:8000}"

# Expand a leading tilde without `eval` (eval echo would re-evaluate the whole
# argument as shell — a path containing $(...) or backticks would be executed).
# `${REPO_PATH/#\~/$HOME}` rewrites only a leading '~' to $HOME, nothing else.
REPO_PATH_EXPANDED="${REPO_PATH/#\~/$HOME}"

# ---------------------------------------------------------------------------
# Validate prerequisites
# ---------------------------------------------------------------------------

if ! command -v git >/dev/null 2>&1; then
    echo "[projecthub-hook] ERROR: git not found in PATH" >&2
    exit 2
fi

if [ ! -d "$REPO_PATH_EXPANDED" ]; then
    echo "[projecthub-hook] ERROR: repo path does not exist: $REPO_PATH_EXPANDED" >&2
    exit 1
fi

# Warn if curl not present (hook will silently skip, but warn at install time)
if ! command -v curl >/dev/null 2>&1; then
    echo "[projecthub-hook] WARNING: curl not found in PATH — hook will silently skip on commit" >&2
fi

# ---------------------------------------------------------------------------
# Resolve hooks directory
# ---------------------------------------------------------------------------

if [ -n "$HOOKS_DIR_OVERRIDE" ]; then
    HOOKS_DIR="$HOOKS_DIR_OVERRIDE"
else
    # git rev-parse --git-common-dir works from both main repo and any worktree:
    # - Main repo:  returns ".git"  (relative, resolved below)
    # - Worktree:   returns absolute path to shared .git of the main repo
    # bare repo detection: if --git-common-dir returns "." it is bare.
    COMMON_DIR=$(git -C "$REPO_PATH_EXPANDED" rev-parse --git-common-dir 2>/dev/null) || {
        echo "[projecthub-hook] ERROR: not a git repository: $REPO_PATH_EXPANDED" >&2
        exit 1
    }

    if [ "$COMMON_DIR" = "." ]; then
        echo "[projecthub-hook] ERROR: bare repositories are not supported" >&2
        exit 1
    fi

    # Resolve to absolute path
    case "$COMMON_DIR" in
        /*) HOOKS_DIR="$COMMON_DIR/hooks" ;;
        *)  HOOKS_DIR="$REPO_PATH_EXPANDED/$COMMON_DIR/hooks" ;;
    esac
fi

mkdir -p "$HOOKS_DIR"

# ---------------------------------------------------------------------------
# Build the block to inject
# ---------------------------------------------------------------------------

BLOCK_BEGIN="# === BEGIN projecthub-refresh (DO NOT EDIT — installed by scripts/install-git-hook.sh) ==="
BLOCK_END="# === END projecthub-refresh ==="
# Grep-safe pattern (avoid special chars in grep -F context — -F handles this already,
# but for awk we need a simple literal substring the hook line always contains)
BLOCK_BEGIN_GREP="BEGIN projecthub-refresh"

# The curl body. Use single quotes to avoid shell escaping inside the hook.
CURL_LINE="(command -v curl >/dev/null 2>&1 && curl -fsS -m 3 -X POST -H 'X-Git-Refresh-Token: ${SECRET}' '${BACKEND_URL}/api/boards/${BOARD_KEY}/git/refresh' >/dev/null 2>&1 &) || true"

# ---------------------------------------------------------------------------
# Helper: install or update the block in a single hook file
# ---------------------------------------------------------------------------

install_hook() {
    HOOK_FILE="$1"
    HOOK_NAME=$(basename "$HOOK_FILE")

    if [ ! -f "$HOOK_FILE" ]; then
        # No existing hook — create fresh with shebang + block
        {
            printf '#!/bin/sh\n'
            printf '%s\n' "$BLOCK_BEGIN"
            printf '%s\n' "$CURL_LINE"
            printf '%s\n' "$BLOCK_END"
        } > "$HOOK_FILE"
        chmod +x "$HOOK_FILE"
        echo "[projecthub-hook] installed $HOOK_NAME"
        return
    fi

    # File exists — check if our marker block is already there
    if grep -qF "$BLOCK_BEGIN" "$HOOK_FILE" 2>/dev/null; then
        # Block exists — extract the current curl line (line after BEGIN marker)
        CURRENT_CURL=$(awk "/$BLOCK_BEGIN_GREP/{getline; print; exit}" "$HOOK_FILE" 2>/dev/null || true)

        if [ "$CURRENT_CURL" = "$CURL_LINE" ]; then
            echo "[projecthub-hook] already installed (matched) $HOOK_NAME"
            return
        fi

        # Block exists but content differs — replace in-place using temp file + POSIX tools.
        # Strategy: write everything before BEGIN, then write new block, then everything after END.
        TMPFILE="${HOOK_FILE}.phrtmp.$$"
        INSIDE_BLOCK=0
        BLOCK_WRITTEN=0
        # `|| [ -n "$LINE" ]` processes a final line that lacks a trailing
        # newline: `read` returns non-zero at EOF but still fills LINE, so
        # without this guard the last line of a hook would be silently dropped.
        while IFS= read -r LINE || [ -n "$LINE" ]; do
            case "$LINE" in
                *"BEGIN projecthub-refresh"*)
                    INSIDE_BLOCK=1
                    if [ "$BLOCK_WRITTEN" = "0" ]; then
                        printf '%s\n' "$BLOCK_BEGIN" >> "$TMPFILE"
                        printf '%s\n' "$CURL_LINE"  >> "$TMPFILE"
                        printf '%s\n' "$BLOCK_END"  >> "$TMPFILE"
                        BLOCK_WRITTEN=1
                    fi
                    ;;
                *"END projecthub-refresh"*)
                    INSIDE_BLOCK=0
                    ;;
                *)
                    if [ "$INSIDE_BLOCK" = "0" ]; then
                        printf '%s\n' "$LINE" >> "$TMPFILE"
                    fi
                    ;;
            esac
        done < "$HOOK_FILE"
        mv "$TMPFILE" "$HOOK_FILE"
        chmod +x "$HOOK_FILE"
        echo "[projecthub-hook] updated $HOOK_NAME"
    else
        # Block not present — append to existing hook
        {
            printf '\n'
            printf '%s\n' "$BLOCK_BEGIN"
            printf '%s\n' "$CURL_LINE"
            printf '%s\n' "$BLOCK_END"
        } >> "$HOOK_FILE"
        chmod +x "$HOOK_FILE"
        echo "[projecthub-hook] installed $HOOK_NAME (appended to existing)"
    fi
}

# ---------------------------------------------------------------------------
# Install into all four hook files
# ---------------------------------------------------------------------------

for HOOK_NAME in post-commit post-merge post-checkout post-rewrite; do
    install_hook "$HOOKS_DIR/$HOOK_NAME"
done

echo "[projecthub-hook] done. Hooks directory: $HOOKS_DIR"

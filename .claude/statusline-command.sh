#!/usr/bin/env bash
input=$(cat)

# ANSI colors (reset after each use)
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

user=$(whoami)

raw_cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // ""')
cwd_basename=$(basename "$raw_cwd")

# Git info — skip optional locks to avoid stale-index warnings
branch=$(GIT_OPTIONAL_LOCKS=0 git -C "$raw_cwd" rev-parse --abbrev-ref HEAD 2>/dev/null)

git_segment=""
if [ -n "$branch" ]; then
  staged=$(GIT_OPTIONAL_LOCKS=0 git -C "$raw_cwd" diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
  unstaged=$(GIT_OPTIONAL_LOCKS=0 git -C "$raw_cwd" diff --numstat 2>/dev/null | wc -l | tr -d ' ')
  counts=""
  [ "$staged" -gt 0 ] 2>/dev/null && counts="${counts} +${staged}"
  [ "$unstaged" -gt 0 ] 2>/dev/null && counts="${counts} ~${unstaged}"
  git_segment=$(printf "${GREEN}(%s%s)${RESET}" "$branch" "$counts")
fi

model=$(echo "$input" | jq -r '.model.display_name // ""')

used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
total_input=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
ctx_segment=""
if [ -n "$used" ]; then
  pct=$(printf '%.0f' "$used")
  tokens_display=""
  if [ -n "$total_input" ]; then
    # Format as k (thousands) for compactness
    tokens_display=$(printf " (%sk)" "$(echo "$total_input" | awk '{printf "%.1f", $1/1000}')")
  fi
  if [ "$pct" -gt 80 ] 2>/dev/null; then
    ctx_segment=$(printf "${RED}ctx: %s%%%s${RESET}" "$pct" "$tokens_display")
  else
    ctx_segment=$(printf "${YELLOW}ctx: %s%%%s${RESET}" "$pct" "$tokens_display")
  fi
fi

# Item 6 — session cost (omit when absent/null)
CYAN='\033[0;36m'
cost_raw=$(echo "$input" | jq -r 'if (.cost.total_cost_usd != null) then .cost.total_cost_usd else empty end')
cost_segment=""
if [ -n "$cost_raw" ]; then
  cost_segment=$(printf "${CYAN}\$%.2f${RESET}" "$cost_raw")
fi

# Item 7 — rate limit 5-hour used % (omit when absent/null; magenta)
MAGENTA='\033[0;35m'
five_h=$(echo "$input" | jq -r 'if (.rate_limits.five_hour.used_percentage != null) then .rate_limits.five_hour.used_percentage else empty end')
rate_segment=""
if [ -n "$five_h" ]; then
  rate_pct=$(printf '%.0f' "$five_h")
  rate_segment=$(printf "${MAGENTA}5h:%s%%${RESET}" "$rate_pct")
fi

# Build output using printf to preserve ANSI escapes
parts="${user} ${cwd_basename}"
[ -n "$git_segment" ] && parts="${parts} ${git_segment}"
[ -n "$model" ] && parts="${parts} | ${model}"
[ -n "$ctx_segment" ] && parts="${parts} | ${ctx_segment}"
[ -n "$cost_segment" ] && parts="${parts} | ${cost_segment}"
[ -n "$rate_segment" ] && parts="${parts} | ${rate_segment}"

printf "%s\n" "$parts"

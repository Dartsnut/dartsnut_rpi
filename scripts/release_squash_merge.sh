#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

EXCLUDED_PATHS=(
  "scripts/"
  "supabase/"
  "supabase_bridge/"
  ".agents/"
  ".cursor/"
  "skills-lock.json"
  "tests/integration/"
  "docs/supabase-sync.md"
)

log() {
  printf '[release-squash] %s\n' "$*"
}

fail() {
  printf '[release-squash] ERROR: %s\n' "$*" >&2
  exit 1
}

require_clean_tree() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "working tree must be clean before running"
  fi
}

require_branch_exists() {
  local branch="$1"
  git show-ref --verify --quiet "refs/heads/${branch}" || fail "missing local branch: ${branch}"
}

require_remote_branch_exists() {
  local remote_ref="$1"
  git show-ref --verify --quiet "refs/remotes/${remote_ref}" || fail "missing remote branch: ${remote_ref}"
}

normalize_version_input() {
  local raw="$1"
  raw="${raw#v}"
  if [[ ! "${raw}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    fail "version must match X.Y.Z or vX.Y.Z; got: ${1}"
  fi
  printf '%s' "${raw}"
}

next_patch_from_release_tags() {
  local tags latest line base
  tags="$(git tag --merged "origin/release")"
  latest=""
  while IFS= read -r line; do
    [[ "${line}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    if [[ -z "${latest}" ]]; then
      latest="${line}"
      continue
    fi
    if [[ "$(printf '%s\n%s\n' "${latest#v}" "${line#v}" | sort -V | tail -n 1)" == "${line#v}" ]]; then
      latest="${line}"
    fi
  done <<< "${tags}"

  if [[ -z "${latest}" ]]; then
    fail "no semver tags (vX.Y.Z) found merged into origin/release"
  fi

  base="${latest#v}"
  IFS='.' read -r major minor patch <<< "${base}"
  patch=$((patch + 1))
  printf '%s.%s.%s' "${major}" "${minor}" "${patch}"
}

restore_excluded_paths() {
  local path
  for path in "${EXCLUDED_PATHS[@]}"; do
    git restore --source=HEAD --staged --worktree -- "${path}" 2>/dev/null || true
  done
}

main() {
  local input_version resolved_version commit_message
  input_version="${1:-}"

  require_clean_tree
  require_branch_exists "master"
  require_branch_exists "release"
  require_remote_branch_exists "origin/release"

  if [[ -n "${input_version}" ]]; then
    resolved_version="$(normalize_version_input "${input_version}")"
  else
    resolved_version="$(next_patch_from_release_tags)"
  fi
  commit_message="Release version v${resolved_version}"

  log "using version: v${resolved_version}"
  log "checking out release and syncing with origin/release"
  git checkout release
  git merge --ff-only origin/release

  log "starting squash merge from master into release"
  if ! git merge --squash master; then
    log "merge reported conflicts, resolving with master-preferred strategy"
    git checkout --theirs -- .
    git add -A
  fi

  log "excluding sensitive paths from staged release commit"
  restore_excluded_paths

  if git diff --cached --quiet; then
    fail "no changes staged after exclusions; nothing to commit"
  fi

  log "final staged diff summary"
  git --no-pager diff --cached --stat

  log "creating squash commit"
  git commit -m "${commit_message}"
  log "done: $(git rev-parse --short HEAD)"
}

main "$@"

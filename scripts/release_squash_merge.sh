#!/usr/bin/env bash
#
# Squash-merge master into release with an explicit device allowlist.
#
# Pi layout (see setup.sh / update.sh): repo lives at /home/rpi/dartsnut_rpi.
# Release branch carries only what is needed to run main.py, setup.sh, update.sh,
# systemd units, assets, Python trees, requirements, system-packages, and the
# compiled Supabase sync binary at ./bridge (no supabase/ or supabase_bridge/ in
# the release commit).
#
# Flow: checkout release -> ff-only origin/release -> merge --squash master ->
# optional cargo bridge build -> clear index -> stage allowlist only -> verify ->
# single commit -> tag vX.Y.Z.
#
# Environment:
#   BUILD_BRIDGE=1        — run scripts/compile_supabase_bridge.sh after squash
#                           (default: skip bridge build and reuse existing ./bridge).
#   SKIP_GIT_FETCH=1      — do not run git fetch origin before branch checks.
#   RELEASE_PUSH=1        — git push origin release and the version tag after commit.
#
# Usage:
#   scripts/release_squash_merge.sh [vX.Y.Z|X.Y.Z]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths included in the release commit (minimal device tree).
ALLOWLIST=(
  .gitignore
  main.py
  check_and_update.py
  default.py
  assets.py
  widget_lifecycle.py
  game_lifecycle.py
  machine_state_service.py
  network_utils.py
  remote_sync_bridge.py
  supabase_sync_bridge.py
  setup.sh
  update.sh
  core
  domain
  python_ble
  python_websocket
  runtime
  states
  assets_media
  DartsnutRGBMatrix
  services/dartsnut_matrix.service
  services/dartsnut_python.service
  services/99-dartsnut-tcp.conf
  services/logo.ppm
  bridge
  requirements.txt
  system-packages.txt
  scripts/install_system_packages.sh
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

maybe_fetch_origin() {
  if [[ "${SKIP_GIT_FETCH:-}" == "1" ]]; then
    log "SKIP_GIT_FETCH=1: skipping git fetch origin"
    return 0
  fi
  log "git fetch origin"
  git fetch origin
}

build_bridge_if_enabled() {
  if [[ "${BUILD_BRIDGE:-}" != "1" ]]; then
    log "BUILD_BRIDGE not set: skipping bridge compile (default)"
    return 0
  fi
  if [[ ! -d "${REPO_ROOT}/supabase_bridge" ]]; then
    fail "supabase_bridge/ missing in working tree; cannot compile bridge"
  fi
  log "BUILD_BRIDGE=1: building bridge (scripts/compile_supabase_bridge.sh)"
  "${REPO_ROOT}/scripts/compile_supabase_bridge.sh"
}

require_allowlist_paths_exist() {
  local p missing=0
  for p in "${ALLOWLIST[@]}"; do
    if [[ ! -e "${REPO_ROOT}/${p}" ]]; then
      printf '[release-squash] missing path after merge/build: %s\n' "${p}" >&2
      missing=1
    fi
  done
  if [[ "${missing}" -ne 0 ]]; then
    fail "one or more allowlist paths are missing; fix master or allowlist"
  fi
}

stage_allowlist_only() {
  local p
  log "clearing index and staging allowlist only"
  # Unstage squash result, keep merged working tree.
  git reset HEAD
  # Drop all tracked paths from index; working tree unchanged.
  while IFS= read -r -d '' f; do
    git rm -f --cached -- "${f}"
  done < <(git ls-files -z)
  for p in "${ALLOWLIST[@]}"; do
    git add -- "${p}"
  done
}

assert_index_excludes_supabase_trees() {
  local bad
  bad="$(git diff --cached --name-only | awk '
    /^supabase\// { print }
    /^supabase_bridge\// { print }
  ' || true)"
  if [[ -n "${bad}" ]]; then
    printf '%s\n' "${bad}" >&2
    fail "staged changes include supabase/ or supabase_bridge/ (forbidden)"
  fi
}

assert_scripts_allowlist_only() {
  local extra
  extra="$(git diff --cached --name-only | awk '/^scripts\// && $0 != "scripts/install_system_packages.sh" { print }' || true)"
  if [[ -n "${extra}" ]]; then
    printf '%s\n' "${extra}" >&2
    fail "unexpected scripts/ paths staged (only scripts/install_system_packages.sh allowed)"
  fi
}

tag_release_commit() {
  local version="$1"
  local tag_name="v${version}"
  if git rev-parse -q --verify "refs/tags/${tag_name}" >/dev/null; then
    fail "tag ${tag_name} already exists"
  fi
  log "tagging commit as ${tag_name}"
  git tag "${tag_name}"
}

maybe_push_release() {
  local version="${1:-}"
  if [[ "${RELEASE_PUSH:-}" != "1" ]]; then
    return 0
  fi
  log "RELEASE_PUSH=1: pushing origin release"
  git push origin release
  if [[ -n "${version}" ]]; then
    log "RELEASE_PUSH=1: pushing tag v${version}"
    git push origin "v${version}"
  fi
}

main() {
  local input_version resolved_version commit_message
  input_version="${1:-}"

  require_clean_tree
  require_branch_exists "master"
  require_branch_exists "release"
  maybe_fetch_origin
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

  build_bridge_if_enabled
  require_allowlist_paths_exist
  stage_allowlist_only

  assert_index_excludes_supabase_trees
  assert_scripts_allowlist_only

  if git diff --cached --quiet; then
    fail "no changes staged after allowlist; nothing to commit"
  fi

  log "final staged diff summary"
  git --no-pager diff --cached --stat

  log "creating squash commit"
  git commit -m "${commit_message}"
  tag_release_commit "${resolved_version}"
  log "done: $(git rev-parse --short HEAD) (v${resolved_version})"

  maybe_push_release "${resolved_version}"
}

main "$@"

#!/usr/bin/env bash
#
# Squash-merge master into release with an explicit device allowlist.
#
# Pi layout (see setup.sh / update.sh): repo lives at /home/rpi/dartsnut_rpi.
# Release branch carries only what is needed to run main.py, setup.sh, update.sh,
# systemd units, assets, Python trees (including core/app_defaults and runtime/sync),
# vendored ./uv + uv.sha256, pyproject.toml, uv.lock, scripts/uv_env.sh,
# system-packages, and the compiled Supabase binaries at ./bridge and
# ./watchdog (no supabase/, supabase_bridge/, tests/, docs/, or legacy
# requirements.txt in the release commit).
#
# Flow: resolve version -> dry-run report if requested, otherwise bump
# pyproject.toml and uv.lock on master and commit -> checkout release ->
# ff-only origin/release -> merge --squash master -> optional cargo bridge/worker build ->
# clear index -> stage allowlist only -> verify -> single commit -> tag vX.Y.Z.
#
# Environment:
#   BUILD_BRIDGE=1        — run scripts/compile_supabase_bridge.sh after squash
#                           (default: skip build and reuse existing ./bridge and
#                           ./watchdog).
#   DRY_RUN=1             — inspect release inputs without changing branches,
#                           commits, tags, or the working tree.
#   SKIP_GIT_FETCH=1      — do not run git fetch origin before branch checks.
#   RELEASE_PUSH=1        — git push origin release and the version tag after commit.
#
# Usage:
#   scripts/release_squash_merge.sh [--dry-run] [vX.Y.Z|X.Y.Z]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Paths included in the release commit (minimal device tree).
ALLOWLIST=(
  .gitattributes
  .gitignore
  README.md
  uv
  uv.sha256
  main.py
  check_and_update.py
  default.py
  assets.py
  widget_lifecycle.py
  game_lifecycle.py
  machine_state_service.py
  network_utils.py
  community_api.py
  pico8_sync.py
  preview_cache.py
  validation_worker.py
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
  mcp
  mcp_server
  assets_media
  DartsnutRGBMatrix
  services/dartsnut_matrix.service
  services/dartsnut_python.service
  services/dartsnut_mcp.service
  services/dartsnut_watchdog.service
  services/99-dartsnut-tcp.conf
  services/logo.ppm
  bridge
  watchdog
  pyproject.toml
  uv.lock
  system-packages.txt
  scripts/install_system_packages.sh
  scripts/uv_env.sh
)

# Top-level folders intentionally stripped from release commits. Dry-run warnings
# focus on added files that might belong on the device, so these stay quiet.
STRIPPED_RELEASE_FOLDERS=(
  docs
  openspec
  scripts
  supabase
  supabase_bridge
  tests
)

DRY_RUN="${DRY_RUN:-0}"
INPUT_VERSION=""

log() {
  printf '[release-squash] %s\n' "$*" >&2
}

fail() {
  printf '[release-squash] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/release_squash_merge.sh [--dry-run] [vX.Y.Z|X.Y.Z]

Environment:
  BUILD_BRIDGE=1    Build bridge before staging release allowlist.
  DRY_RUN=1         Inspect release inputs without changing git state.
  SKIP_GIT_FETCH=1  Skip git fetch origin.
  RELEASE_PUSH=1    Push release branch and tag after commit.
EOF
}

parse_args() {
  local arg
  while [[ "$#" -gt 0 ]]; do
    arg="$1"
    case "${arg}" in
      --dry-run)
        DRY_RUN=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      -*)
        fail "unknown option: ${arg}"
        ;;
      *)
        if [[ -n "${INPUT_VERSION}" ]]; then
          fail "unexpected extra argument: ${arg}"
        fi
        INPUT_VERSION="${arg}"
        ;;
    esac
    shift
  done
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
      continue
    fi
    if [[ "${p}" == "uv" && ! -x "${REPO_ROOT}/${p}" ]]; then
      printf '[release-squash] vendored uv is not executable: %s\n' "${p}" >&2
      missing=1
    fi
  done
  if [[ "${missing}" -ne 0 ]]; then
    fail "one or more allowlist paths are missing or invalid; fix master or allowlist"
  fi
}

is_allowlisted_path() {
  local path="$1"
  local allowed
  for allowed in "${ALLOWLIST[@]}"; do
    if [[ "${path}" == "${allowed}" || "${path}" == "${allowed}/"* ]]; then
      return 0
    fi
  done
  return 1
}

is_always_stripped_release_path() {
  local path="$1"
  local stripped
  for stripped in "${STRIPPED_RELEASE_FOLDERS[@]}"; do
    if [[ "${path}" == "${stripped}/"* ]]; then
      return 0
    fi
  done
  return 1
}

warn_added_paths_outside_allowlist() {
  local base_ref="$1"
  local source_ref="$2"
  local path
  local warned=0

  log "dry-run: checking added files outside release allowlist (${base_ref}..${source_ref})"
  while IFS= read -r path; do
    [[ -n "${path}" ]] || continue
    if is_allowlisted_path "${path}"; then
      continue
    fi
    if is_always_stripped_release_path "${path}"; then
      continue
    fi
    if [[ "${warned}" -eq 0 ]]; then
      log "dry-run warning: added files not covered by release allowlist"
      warned=1
    fi
    printf '[release-squash]   %s\n' "${path}" >&2
  done < <(git diff --name-only --diff-filter=A "${base_ref}..${source_ref}")

  if [[ "${warned}" -eq 0 ]]; then
    log "dry-run: no added files outside release allowlist"
  fi
}

stage_allowlist_only() {
  local p
  log "clearing index and staging allowlist only"
  log "note: following 'rm' lines are git rm --cached (index only); files remain on disk"
  # Unstage squash result, keep merged working tree.
  git reset HEAD
  # Drop all tracked paths from index; working tree unchanged.
  while IFS= read -r -d '' f; do
    git rm -f --cached -- "${f}" >/dev/null 2>&1 || true
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
  extra="$(git diff --cached --name-only | awk '
    /^scripts\// &&
      $0 != "scripts/install_system_packages.sh" &&
      $0 != "scripts/uv_env.sh" { print }
  ' || true)"
  if [[ -n "${extra}" ]]; then
    printf '%s\n' "${extra}" >&2
    fail "unexpected scripts/ paths staged (only scripts/install_system_packages.sh and scripts/uv_env.sh allowed)"
  fi
}

assert_index_excludes_legacy_requirements() {
  # Staging a deletion of requirements.txt is correct; staging its content is not.
  local legacy
  legacy="$(git diff --cached --name-only --diff-filter=ACMR | awk '
    $0 == "requirements.txt" || $0 == "requirement.txt" { print }
  ' || true)"
  if [[ -n "${legacy}" ]]; then
    printf '%s\n' "${legacy}" >&2
    fail "staged changes include legacy requirements.txt (replaced by pyproject.toml + uv.lock)"
  fi
}

assert_release_imports_resolve() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' EXIT INT TERM
  git checkout-index --prefix="${tmpdir}/" -a
  log "verifying release import graph from staged allowlist"
  PYTHONPATH="${tmpdir}" python3 - <<'PY'
import importlib

for module in (
    "community_api",
    "preview_cache",
    "validation_worker",
    "game_lifecycle",
    "widget_lifecycle",
    "machine_state_service",
    "mcp_server.framebuffer",
    "mcp_server.git_ops",
    "mcp_server.state_snapshot",
    "mcp_server.tools",
):
    importlib.import_module(module)
PY
  rm -rf "${tmpdir}"
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

resolve_squash_conflicts_prefer_master() {
  # During `git merge --squash master` on release, unmerged paths must match master.
  local f leftover
  log "merge reported conflicts, resolving with master-preferred strategy"
  while IFS= read -r -d '' f; do
    if git show "master:${f}" >/dev/null 2>&1; then
      log "conflict: using master for ${f}"
      git checkout "master" -- "${f}"
    else
      log "conflict: removing ${f} (absent on master)"
      git rm -f -- "${f}" 2>/dev/null || true
    fi
  done < <(git diff -z --name-only --diff-filter=U)
  git add -A
  leftover="$(git diff --name-only --diff-filter=U || true)"
  if [[ -n "${leftover}" ]]; then
    printf '%s\n' "${leftover}" >&2
    fail "unmerged paths remain after conflict resolution"
  fi
  log "conflicts resolved; continuing squash merge"
}

bump_pyproject_version_on_master() {
  local version="$1"
  local pyproject="${REPO_ROOT}/pyproject.toml"
  local uv_lock="${REPO_ROOT}/uv.lock"
  local current_pyproject current_uv_lock

  log "checking out master and syncing with origin/master"
  git checkout master
  if git show-ref --verify --quiet "refs/remotes/origin/master"; then
    git merge --ff-only origin/master
  fi

  if [[ ! -f "${pyproject}" ]]; then
    fail "missing ${pyproject}"
  fi
  if [[ ! -f "${uv_lock}" ]]; then
    fail "missing ${uv_lock}"
  fi

  current_pyproject="$(grep -E '^version = ' "${pyproject}" | sed -n 's/^version = "\(.*\)"/\1/p' | head -n 1)"
  if [[ -z "${current_pyproject}" ]]; then
    fail "pyproject.toml missing version = \"X.Y.Z\" field"
  fi

  current_uv_lock="$(python3 -c "
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
match = re.search(r'name = \"dartsnut-rpi\"\nversion = \"([^\"]+)\"', text)
if match is None:
    raise SystemExit('uv.lock missing dartsnut-rpi package version')
print(match.group(1))
" "${uv_lock}")"

  if [[ "${current_pyproject}" == "${version}" && "${current_uv_lock}" == "${version}" ]]; then
    log "pyproject.toml and uv.lock already at ${version}; skipping master bump commit"
    return 0
  fi

  log "updating project version -> ${version} (pyproject.toml: ${current_pyproject}, uv.lock: ${current_uv_lock})"
  python3 -c "
import pathlib
import re
import sys

resolved = sys.argv[1]
pyproject = pathlib.Path(sys.argv[2])
uv_lock = pathlib.Path(sys.argv[3])

py_text = pyproject.read_text(encoding='utf-8')
py_new, py_n = re.subn(
    r'^version = \".*\"',
    f'version = \"{resolved}\"',
    py_text,
    count=1,
    flags=re.M,
)
if py_n != 1:
    raise SystemExit('failed to update version in pyproject.toml')
pyproject.write_text(py_new, encoding='utf-8')

lock_text = uv_lock.read_text(encoding='utf-8')
lock_new, lock_n = re.subn(
    r'(name = \"dartsnut-rpi\"\nversion = \")[^\"]+(\")',
    rf'\\g<1>{resolved}\\2',
    lock_text,
    count=1,
)
if lock_n != 1:
    raise SystemExit('failed to update dartsnut-rpi version in uv.lock')
uv_lock.write_text(lock_new, encoding='utf-8')
" "${version}" "${pyproject}" "${uv_lock}"

  git add -- "${pyproject}" "${uv_lock}"
  git commit -m "chore(release): set project version to ${version}"
  log "master version commit: $(git rev-parse --short HEAD)"
}

run_dry_run() {
  local resolved_version="$1"

  log "DRY_RUN=1: no branches, commits, tags, pushes, or working-tree files will be changed"
  log "would use version: v${resolved_version}"
  warn_added_paths_outside_allowlist "origin/release" "master"
  log "dry-run: diff summary for origin/release..master"
  git --no-pager diff --stat "origin/release..master"
  log "dry-run complete"
}

main() {
  local resolved_version commit_message
  parse_args "$@"

  require_branch_exists "master"
  require_branch_exists "release"
  maybe_fetch_origin
  require_remote_branch_exists "origin/release"

  if [[ -n "${INPUT_VERSION}" ]]; then
    resolved_version="$(normalize_version_input "${INPUT_VERSION}")"
  else
    resolved_version="$(next_patch_from_release_tags)"
  fi
  commit_message="Release version v${resolved_version}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    run_dry_run "${resolved_version}"
    return 0
  fi

  require_clean_tree

  log "using version: v${resolved_version}"
  bump_pyproject_version_on_master "${resolved_version}"

  log "checking out release and syncing with origin/release"
  git checkout release
  git merge --ff-only origin/release

  log "starting squash merge from master into release"
  if ! git merge --squash master; then
    resolve_squash_conflicts_prefer_master
  fi

  build_bridge_if_enabled
  require_allowlist_paths_exist
  stage_allowlist_only

  assert_index_excludes_supabase_trees
  assert_scripts_allowlist_only
  assert_index_excludes_legacy_requirements
  assert_release_imports_resolve

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

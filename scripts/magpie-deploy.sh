#!/bin/bash
# Magpie Artifact Storage - Deployment Script
#
# Installs and manages magpie on Debian 12/13 using Docker Compose.
#
# Usage:
#   magpie-deploy.sh install [options]    Install magpie
#   magpie-deploy.sh update               Update to latest version
#   magpie-deploy.sh uninstall [options]  Remove magpie
#   magpie-deploy.sh status               Show service status
#   magpie-deploy.sh logs [options]       View container logs
#
# For detailed help: magpie-deploy.sh --help

set -euo pipefail

# Env-var form of --release (see parse_args), captured before the
# Constants section below initializes MAGPIE_VERSION for its own,
# unrelated purpose (the semver string detected from the cloned
# pyproject.toml, used to pick the GHCR image tag). An inherited
# MAGPIE_VERSION environment variable is read here first, so
# `MAGPIE_VERSION=vX.Y.Z magpie-deploy.sh install` can still select a
# release even though the name is reused below for a different value once
# the repo is cloned. GITHUB_REF is also honored. The raw value is used
# as-is here; resolve_and_validate_release() (defined later, once
# log_warn/die exist) strips any "refs/tags/"/"refs/heads/" prefix and
# rejects other ref namespaces. See issue #559.
REQUESTED_RELEASE_ENV="${MAGPIE_VERSION:-${GITHUB_REF:-}}"

# =============================================================================
# Constants
# =============================================================================

SCRIPT_NAME="$(basename "$0")"
GITHUB_REPO="SouthwestCCDC/magpie"
DEFAULT_GITHUB_BRANCH="default"
GITHUB_BRANCH="$DEFAULT_GITHUB_BRANCH"
GHCR_IMAGE="ghcr.io/southwestccdc/magpie"
MAGPIE_VERSION=""  # Dynamically detected from pyproject.toml after cloning repo
# For --version output before repo clone (script's own version, not a
# release selector), display "dev" (cosmetic only).
HARDCODED_VERSION="dev"

# Default configuration
DEFAULT_INSTALL_DIR="/opt/magpie"
DEFAULT_HTTP_PORT="8080"
# Trust no proxy by default -- the bundled Caddy uses the real connecting
# peer's IP. Only set (via --trusted-proxies or the interactive prompt) to
# the exact upstream hop(s) when it sits behind another reverse proxy. A
# broad range here would let any client on it spoof X-Forwarded-For and
# defeat MAGPIE_ALLOWED_CIDRS. See issue #575.
DEFAULT_TRUSTED_PROXIES=""
# The bundled compose file has no Compose-level default for this (fail-
# closed) -- the installer always supplies one so a fresh install or an
# update never boot-loops. 'file' is the frictionless choice for a
# self-managed install; operators who want exec/discard/stdout edit .env
# afterwards. See docs/installation.md "Admin Token Delivery".
DEFAULT_ADMIN_TOKEN_SINK="file"
DEFAULT_ADMIN_TOKEN_SINK_FILE_PATH="/data/admin-token"

# =============================================================================
# Global variables (populated during config)
# =============================================================================

INSTALL_DIR=""
DATA_DIR=""
HTTP_PORT=""
TRUSTED_PROXIES=""
# Whether --trusted-proxies was passed explicitly on this run (as opposed to
# TRUSTED_PROXIES having been populated only by load_existing_config()'s
# legacy-key migration). Distinguishes an operator's deliberate override
# from a passive carry-forward so cmd_update's .env surgery can update an
# already-persisted MAGPIE_TRUSTED_PROXIES in place instead of leaving it
# untouched. See issue #583.
TRUSTED_PROXIES_FROM_CLI="false"
# Read-only mirror of MAGPIE_ALLOWED_CIDRS from an existing install's .env --
# this script never sets or persists it (docker-compose passes it straight
# through to the app); it's only loaded so cmd_update/cmd_install can warn
# or gate when it's paired with an empty MAGPIE_TRUSTED_PROXIES. See issue
# #579.
ALLOWED_CIDRS=""
# Whether MAGPIE_TRUSTED_PROXIES (or the legacy unprefixed TRUSTED_PROXIES)
# key was present in the existing install's .env at the start of this run,
# before load_existing_config()'s migration/writeback touches anything.
# Presence -- even an explicit empty value -- is the deliberate-choice
# signal the issue #579 gate uses to fire only once. See
# load_existing_config().
TRUSTED_PROXIES_KEY_PRESENT="false"
BIND_IP=""
# Whether --bind-ip was passed explicitly on this run -- same purpose as
# TRUSTED_PROXIES_FROM_CLI above: load_existing_config() must not let a
# stale/absent .env value clobber a CLI override before
# reconcile_env_file_for_update() ever sees it. See issue #583's precedent
# and the A1 review finding on PR #597.
BIND_IP_FROM_CLI="false"
# Read-only detection of a persisted (pre-0.2.0) TLS_MODE from an existing
# install's .env -- never used to drive any generated config, only to
# decide whether cmd_update's tier-2 deprecation gate fires. See
# load_existing_config() and cmd_update().
PERSISTED_TLS_MODE=""
NONINTERACTIVE="false"
FORCE="false"
PURGE="false"
YES="false"
FOLLOW="false"
LINES="100"
FROM_SOURCE="false"
REQUESTED_RELEASE=""  # from --release; empty means "use the default branch"
# --accept-empty-trusted-proxies: acknowledges that an empty
# MAGPIE_TRUSTED_PROXIES is intentional (magpie is directly exposed), so
# the issue #579 gate proceeds instead of prompting/dying. See
# warn_or_gate_trusted_proxies_for_cidr_allow().
ACCEPT_EMPTY_TRUSTED_PROXIES="false"
# --accept-builtin-tls-removed (update only): acknowledges that this
# install's persisted TLS_MODE shows it was terminating TLS itself before
# v0.2.0's bundled image (which never does) replaces it. See cmd_update()'s
# tier-2 deprecation gate.
ACCEPT_BUILTIN_TLS_REMOVED="false"

# =============================================================================
# Helper functions
# =============================================================================

log() {
    echo "[magpie] $*"
}

# `echo -e` (not plain `echo`) so a `\n` embedded in a die()/log_error()
# message renders as an actual line break instead of a literal backslash-n
# -- several call sites below build multi-line messages this way. See
# issue #161.
log_error() {
    echo -e "[magpie] ERROR: $*" >&2
}

log_warn() {
    echo "[magpie] WARNING: $*" >&2
}

die() {
    log_error "$@"
    exit 1
}

confirm() {
    local prompt="$1"
    if [[ "$YES" == "true" ]]; then
        return 0
    fi
    if [[ "$NONINTERACTIVE" == "true" ]]; then
        die "Cannot prompt in noninteractive mode. Use --yes to skip confirmations."
    fi
    read -r -p "[magpie] $prompt [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_value() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        # Use default in noninteractive mode
        printf -v "$var_name" '%s' "$default"
        return
    fi

    local display_default=""
    [[ -n "$default" ]] && display_default=" [$default]"

    read -r -p "[magpie] $prompt$display_default: " value
    if [[ -z "$value" ]]; then
        printf -v "$var_name" '%s' "$default"
    else
        printf -v "$var_name" '%s' "$value"
    fi
}

prompt_choice() {
    local prompt="$1"
    local options="$2"  # Space-separated
    local default="$3"
    local var_name="$4"

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        printf -v "$var_name" '%s' "$default"
        return
    fi

    echo "[magpie] $prompt"
    local i=1
    local opt_array=()
    for opt in $options; do
        opt_array+=("$opt")
        local marker=""
        [[ "$opt" == "$default" ]] && marker=" (default)"
        echo "  $i) $opt$marker"
        ((i++))
    done

    while true; do
        read -r -p "[magpie] Enter choice [1-${#opt_array[@]}]: " choice
        if [[ -z "$choice" ]]; then
            printf -v "$var_name" '%s' "$default"
            return
        fi
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#opt_array[@]} )); then
            printf -v "$var_name" '%s' "${opt_array[$((choice-1))]}"
            return
        fi
        echo "[magpie] Invalid choice. Please enter a number between 1 and ${#opt_array[@]}."
    done
}

# =============================================================================
# Prerequisite checks
# =============================================================================

check_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root"
    fi
}

check_os() {
    if [[ ! -f /etc/os-release ]]; then
        die "Cannot detect OS (missing /etc/os-release)"
    fi

    # shellcheck source=/dev/null
    source /etc/os-release

    if [[ "${ID:-}" != "debian" ]]; then
        die "This script requires Debian (found: ${ID:-unknown})"
    fi

    if [[ ! "${VERSION_ID:-}" =~ ^(12|13)$ ]]; then
        die "This script requires Debian 12 or 13 (found: ${VERSION_ID:-unknown})"
    fi

    log "Detected Debian ${VERSION_ID}"
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        die "Docker is not installed. Install Docker first: https://docs.docker.com/engine/install/debian/"
    fi

    if ! docker compose version &>/dev/null; then
        die "Docker Compose v2 is not installed. Install the docker-compose-plugin package."
    fi

    if ! docker info &>/dev/null; then
        die "Docker daemon is not running. Start it with: systemctl start docker"
    fi

    log "Docker and Docker Compose v2 available"
}

check_curl() {
    if ! command -v curl &>/dev/null; then
        die "curl is not installed. Install it with: apt-get install curl"
    fi
    log "curl available"
}

check_git() {
    if ! command -v git &>/dev/null; then
        die "git is not installed. Install it with: apt-get install git"
    fi
    log "git available"
}

check_prerequisites() {
    check_root
    check_os
    check_curl
    check_git
    check_docker
}

# =============================================================================
# Configuration validation
# =============================================================================

# Validation helpers (see issue #448) ----------------------------------------
#
# These are deliberately permissive-but-safe pattern checks rather than
# fully RFC-compliant validators: the goal is to reject anything that could
# be interpreted as shell, sed, or Caddyfile syntax (metacharacters,
# newlines, braces), not to certify that a value is a *routable* hostname
# or IP address.

# Matches a single IPv4 address, optionally with a /NN CIDR prefix.
is_valid_ipv4_cidr() {
    local value="$1"
    [[ "$value" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})(/([0-9]{1,2}))?$ ]] || return 1

    # Force base-10 interpretation with `10#...`: bash arithmetic otherwise
    # treats a leading-zero octet (e.g. "008") as octal, which either
    # misjudges its value (e.g. "017" -> 15) or hard-errors ("008" has no
    # digit 8/9 in octal).
    local octet
    for octet in "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"; do
        (( 10#$octet <= 255 )) || return 1
    done
    if [[ -n "${BASH_REMATCH[6]}" ]]; then
        (( 10#${BASH_REMATCH[6]} <= 32 )) || return 1
    fi
    return 0
}

# Matches a single IPv6 address, optionally with a /NNN CIDR prefix. This is
# a charset/shape check (hex digits and colons only), not a full RFC 4291
# validator -- it exists to reject injection characters.
is_valid_ipv6_cidr() {
    local value="$1"
    local addr="$value"
    local prefix=""

    if [[ "$value" == */* ]]; then
        addr="${value%%/*}"
        prefix="${value#*/}"
        [[ "$prefix" =~ ^[0-9]{1,3}$ ]] && (( 10#$prefix <= 128 )) || return 1
    fi

    [[ "$addr" =~ ^[0-9A-Fa-f:]+$ ]] || return 1
    [[ "$addr" == *:* ]] || return 1
    return 0
}

# Matches a single IP address (v4 or v6), optionally with a CIDR prefix.
is_valid_ip_or_cidr() {
    local value="$1"
    is_valid_ipv4_cidr "$value" || is_valid_ipv6_cidr "$value"
}

# Matches a safe git ref name (branch or tag): alphanumerics, dot,
# underscore, hyphen, and slash, starting and ending with an alphanumeric.
# This is a conservative allowlist, not a full `git check-ref-format`
# implementation -- its job is to keep a user-controlled --release value
# out of `git clone --branch`/`git fetch` as anything but a literal ref
# name. In particular it rejects a leading '-' (which `git` would
# otherwise parse as another option) and '..' or '//' sequences. See
# issue #559.
is_valid_git_ref() {
    local ref="$1"
    [[ "$ref" =~ ^[A-Za-z0-9]([A-Za-z0-9._/-]*[A-Za-z0-9])?$ ]] || return 1
    [[ "$ref" == *..* ]] && return 1
    [[ "$ref" == *//* ]] && return 1
    return 0
}

# Validates a whitespace-separated list of IPv4/IPv6 addresses or CIDRs.
#
# Rejects any control character EXCEPT horizontal tab in the raw value up
# front, before splitting into tokens. Tab is excluded from the rejection
# because it's a legitimate whitespace separator for this
# "whitespace-separated" list (tokenization below already splits on it via
# IFS) -- rejecting it would be a functional regression for tab-separated
# input that previously validated. Newline/CR and other control
# characters are still rejected: a newline sitting *between* two
# otherwise-valid tokens (e.g. "10.0.0.0/8\n192.168.1.1") would otherwise
# pass per-token validation, but it corrupts the generated .env
# (read_env_file() is line-based -- everything after the first newline in
# a value becomes a separate, likely-dropped "line"). MAGPIE_TRUSTED_PROXIES
# reaches Caddy's `trusted_proxies static {$MAGPIE_TRUSTED_PROXIES:}`
# directive as a single process environment variable (via docker-compose,
# not baked into the Caddyfile text), so a corrupted .env is the actual
# failure mode here rather than a split Caddyfile directive. See issues
# #448 and #575.
#
# Tokens are split with `read -ra` rather than `for token in $list`: the
# latter performs pathname expansion (globbing) in addition to
# word-splitting, so a value containing glob characters (e.g. "*") could
# validate unpredictably depending on files in the current directory.
# `read` never globs.
# Strips leading/trailing whitespace from a value. Used wherever an
# operator-editable value (typically from .env) feeds a boolean/comparison
# decision -- e.g. the issue #579 gate's "is this set?" checks -- so a
# whitespace-only or whitespace-padded value is never mistaken for
# meaningfully non-empty, or misses an exact-match comparison it should
# have hit. Matches how the application itself treats MAGPIE_ALLOWED_CIDRS
# (config.py strips each comma-separated segment).
trim_whitespace() {
    local var="$1"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    printf '%s' "$var"
}

is_valid_ip_or_cidr_list() {
    local list="$1"

    local without_tabs="${list//$'\t'/}"
    if [[ "$without_tabs" =~ [[:cntrl:]] ]]; then
        return 1
    fi

    local -a tokens
    read -ra tokens <<< "$list"

    local token
    for token in "${tokens[@]}"; do
        is_valid_ip_or_cidr "$token" || return 1
    done
    return 0
}

# Rejects path values that contain '..' path-traversal segments or any
# character outside a safe allowlist. Paths in this script are written
# unquoted into generated shell/systemd/docker-compose artifacts, so this
# is stricter than "not empty" / "absolute" alone. Appends to the caller's
# 'errors' array (relies on bash's dynamic scoping of locals). See issue #448.
validate_path_value() {
    local path="$1"
    local label="$2"

    if [[ "$path" =~ (^|/)\.\.(/|$) ]]; then
        errors+=("$label must not contain '..' path segments: $path")
    fi

    if ! [[ "$path" =~ ^[A-Za-z0-9._/-]+$ ]]; then
        errors+=("$label contains invalid characters (only letters, digits, '.', '_', '-', '/' are allowed): $path")
    fi
}

# Validates TRUSTED_PROXIES and BIND_IP format. Called both during install
# (via validate_config) and update (to re-validate values loaded from an
# existing .env before they're written back). Appends to the caller's
# 'errors' array (relies on bash's dynamic scoping of locals). See issue
# #448.
validate_network_config() {
    if [[ -n "$TRUSTED_PROXIES" ]] && ! is_valid_ip_or_cidr_list "$TRUSTED_PROXIES"; then
        errors+=("Invalid --trusted-proxies: $TRUSTED_PROXIES (must be a whitespace-separated list of IPv4/IPv6 addresses or CIDRs)")
    fi

    if [[ -n "$BIND_IP" ]]; then
        if [[ "$BIND_IP" == */* ]] || ! is_valid_ip_or_cidr "$BIND_IP"; then
            errors+=("Invalid --bind-ip: $BIND_IP (must be a single IPv4 or IPv6 address, no CIDR prefix)")
        fi
    fi
}

validate_config() {
    local errors=()

    # Port validation
    if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]] || (( HTTP_PORT < 1 || HTTP_PORT > 65535 )); then
        errors+=("Invalid HTTP port: $HTTP_PORT")
    fi

    # Network value validation (TRUSTED_PROXIES, BIND_IP). These values
    # later flow into the generated .env, so they must be validated here,
    # before any file is generated. See issue #448.
    validate_network_config

    # Directory validation
    if [[ -z "$INSTALL_DIR" ]]; then
        errors+=("Install directory cannot be empty")
    elif [[ ! "$INSTALL_DIR" =~ ^/ ]]; then
        errors+=("Install directory must be an absolute path: $INSTALL_DIR")
    else
        validate_path_value "$INSTALL_DIR" "Install directory"
    fi

    # Data directory validation
    if [[ -z "$DATA_DIR" ]]; then
        errors+=("Data directory cannot be empty")
    elif [[ ! "$DATA_DIR" =~ ^/ ]]; then
        errors+=("Data directory must be an absolute path: $DATA_DIR")
    else
        validate_path_value "$DATA_DIR" "Data directory"
    fi

    # Warn about /home with ProtectHome=true
    if [[ "$DATA_DIR" =~ ^/home(/|$) ]]; then
        log_warn "Data directory is under /home: $DATA_DIR"
        log_warn "The GC service uses ProtectHome=true for security hardening."
        log_warn "This will prevent GC from accessing paths under /home."
        log_warn "Consider using a path like /srv/magpie/data or /opt/magpie/data instead."
        log_warn "See https://github.com/SouthwestCCDC/magpie/issues/159 for details."
        errors+=("Data directory under /home is not compatible with GC service hardening")
    fi

    # Writability check
    local parent_dir
    if [[ -d "$DATA_DIR" ]]; then
        parent_dir="$DATA_DIR"
    else
        parent_dir="$(dirname "$DATA_DIR")"
    fi
    if [[ -d "$parent_dir" ]] && [[ ! -w "$parent_dir" ]]; then
        errors+=("Data directory path is not writable: $parent_dir")
    fi

    if [[ ${#errors[@]} -gt 0 ]]; then
        log_error "Configuration validation failed:"
        for err in "${errors[@]}"; do
            echo "  - $err" >&2
        done
        exit 1
    fi
}

# =============================================================================
# Installation state checks
# =============================================================================

check_existing_installation() {
    if [[ -d "$INSTALL_DIR" ]]; then
        if [[ "$FORCE" != "true" ]]; then
            die "Installation directory already exists: $INSTALL_DIR\nUse --force to overwrite, or 'update' to update existing installation."
        fi

        log_warn "Overwriting existing installation at $INSTALL_DIR"

        # Stop existing services gracefully
        if systemctl is-active magpie.service &>/dev/null; then
            log "Stopping existing services..."
            systemctl stop magpie.service || true
        fi
    fi
}

verify_installation() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        die "Magpie is not installed at $INSTALL_DIR\nRun '$SCRIPT_NAME install' first."
    fi

    if [[ ! -f "${INSTALL_DIR}/etc/.env" ]]; then
        die "Configuration not found at ${INSTALL_DIR}/etc/.env"
    fi
}

# Reads a strict KEY=value .env file into the caller's associative array
# without performing any shell expansion, command substitution, sourcing,
# or evaluation of the file's contents. Comment lines (leading '#') and
# blank lines are skipped; lines that do not match a bare KEY=value shape
# are silently ignored -- this also means a value containing an embedded
# newline cannot smuggle in a second "line" that looks like a directive, it
# just becomes inert trailing text or a rejected non-KV line. See issue #448.
read_env_file() {
    local file="$1"
    local -n out_array="$2"
    local line key value

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip a trailing CR so a CRLF-terminated (e.g. Windows-edited)
        # .env doesn't leave a stray \r embedded in the parsed value (e.g.
        # TRUSTED_PROXIES=...\r), which downstream validators would then
        # reject as a control character.
        line="${line%$'\r'}"

        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue

        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # shellcheck disable=SC2034  # out_array is a nameref to the caller's array; shellcheck can't see its use there
            out_array["$key"]="$value"
        fi
    done < "$file"
}

load_existing_config() {
    if [[ -f "${INSTALL_DIR}/etc/.env" ]]; then
        # Parsed with read_env_file (no shell expansion/execution of the
        # file's contents) rather than sourced as a shell script. See #448.
        local -A env_vars=()
        read_env_file "${INSTALL_DIR}/etc/.env" env_vars

        DATA_DIR="${env_vars[MAGPIE_DATA_DIR]:-$DATA_DIR}"
        HTTP_PORT="${env_vars[MAGPIE_HTTP_PORT]:-$HTTP_PORT}"

        # Read-only detection for cmd_update's tier-2 deprecation gate: a
        # persisted TLS_MODE of auto|manual means this install was
        # terminating TLS itself before v0.2.0's bundled image (which never
        # does) replaces it. This key is never written by generate_env_file()
        # or otherwise used to drive any generated config -- see cmd_update().
        PERSISTED_TLS_MODE="$(trim_whitespace "${env_vars[TLS_MODE]:-}")"

        # MAGPIE_BIND_IP (current, prefixed) with a fallback to the legacy
        # unprefixed BIND_IP key (pre-dates the MAGPIE_ prefix convention;
        # see issue #575's TRUSTED_PROXIES precedent below) so an existing
        # install's --bind-ip setting survives an update unchanged.
        # cmd_update migrates the legacy key forward in its .env surgery.
        #
        # Skipped entirely when BIND_IP_FROM_CLI is true: an explicit
        # --bind-ip on THIS run must win over whatever is in .env. Without
        # this guard, a CLI override would be silently clobbered right back
        # to the persisted (or absent/empty) value below, before
        # reconcile_env_file_for_update() ever saw the operator's actual
        # choice -- `update --bind-ip <X>` would be a no-op. See the A1
        # review finding on PR #597.
        if [[ "$BIND_IP_FROM_CLI" != "true" ]]; then
            if [[ -n "${env_vars[MAGPIE_BIND_IP]+set}" ]]; then
                BIND_IP="$(trim_whitespace "${env_vars[MAGPIE_BIND_IP]}")"
            elif [[ -n "${env_vars[BIND_IP]+set}" ]]; then
                BIND_IP="$(trim_whitespace "${env_vars[BIND_IP]}")"
            fi
        fi

        # MAGPIE_TRUSTED_PROXIES (issue #575) replaces the pre-#575
        # unprefixed TRUSTED_PROXIES key. Prefer the new key; fall back to
        # migrating the old one so an existing installation's configured
        # value survives an `update` unchanged (never silently narrowed to
        # empty here -- that could break a fronted deployment relying on
        # it). If the migrated legacy value is exactly the old overly-broad
        # default this issue fixes, warn loudly rather than fix it
        # automatically: only the operator knows the actual upstream hop(s)
        # to scope it to.
        #
        # Skipped when TRUSTED_PROXIES_FROM_CLI is true -- same reasoning as
        # BIND_IP_FROM_CLI above: this is the exact bug issue #583 was
        # supposed to fix (an explicit --trusted-proxies silently clobbered
        # by .env before reconcile_env_file_for_update() ever saw it,
        # because this function ran in between and didn't know about the
        # CLI override). An explicit CLI value, even a deliberately empty
        # one, IS the deliberate choice -- mark TRUSTED_PROXIES_KEY_PRESENT
        # accordingly so the #579 gate doesn't second-guess it.
        if [[ "$TRUSTED_PROXIES_FROM_CLI" == "true" ]]; then
            TRUSTED_PROXIES_KEY_PRESENT="true"
        else
            local legacy_broad_default="127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"

            if [[ -n "${env_vars[MAGPIE_TRUSTED_PROXIES]+set}" ]]; then
                local raw_trusted_proxies="${env_vars[MAGPIE_TRUSTED_PROXIES]}"
                TRUSTED_PROXIES="$(trim_whitespace "$raw_trusted_proxies")"
                # This script's own writeback (below) only ever persists
                # either a real, already-trimmed value or a truly
                # zero-length "" (the deliberate-empty marker for a
                # confirmed direct-exposure choice) -- never whitespace
                # padding. So a RAW value that is whitespace-only (nonzero
                # length, but trims to empty) is not something this script
                # would have written itself; treat it as anomalous /
                # unconfigured rather than trusting it as deliberate, so the
                # gate still fires instead of silently trusting garbage.
                if [[ -z "$raw_trusted_proxies" || -n "$TRUSTED_PROXIES" ]]; then
                    TRUSTED_PROXIES_KEY_PRESENT="true"
                fi
            elif [[ -n "${env_vars[TRUSTED_PROXIES]+set}" ]]; then
                TRUSTED_PROXIES="$(trim_whitespace "${env_vars[TRUSTED_PROXIES]}")"
                if [[ "$TRUSTED_PROXIES" == "$legacy_broad_default" ]]; then
                    log_warn "Migrating pre-#575 TRUSTED_PROXIES from ${INSTALL_DIR}/etc/.env: it is set to the old overly-broad default ($legacy_broad_default), which lets any client on those ranges spoof X-Forwarded-For and bypass MAGPIE_ALLOWED_CIDRS."
                    log_warn "Edit MAGPIE_TRUSTED_PROXIES in ${INSTALL_DIR}/etc/.env to the exact upstream reverse proxy address(es) in front of this install (or leave it empty to trust none), then run '$SCRIPT_NAME update'."
                fi
                # Unlike the prefixed key above, a present legacy key only
                # counts as "already configured" when it carries a real,
                # non-empty value -- pre-#575, Caddy trusted the hardcoded
                # private_ranges regardless of this key, so an empty legacy
                # TRUSTED_PROXIES was dead config, not a deliberate "trust
                # nothing" choice. Leaving TRUSTED_PROXIES_KEY_PRESENT false
                # here lets the issue #579 gate fire for exactly the
                # deployments that were silently relying on private_ranges and
                # would otherwise break unnoticed on upgrade. A non-empty
                # legacy value is already handled above (migrated forward,
                # warned if it's the broad default) and must not also trip
                # the gate.
                # Already trimmed above, so a whitespace-only legacy value
                # (dead config, same as truly empty) doesn't fool this into
                # "true".
                if [[ -n "$TRUSTED_PROXIES" ]]; then
                    TRUSTED_PROXIES_KEY_PRESENT="true"
                fi
            fi
        fi

        # MAGPIE_ALLOWED_CIDRS is never written by this script (only read
        # here, for cmd_update's issue #579 warning below) -- it reaches
        # docker-compose/Caddy straight from .env, which the operator edits
        # directly. Trimmed to match how the application itself treats it
        # (config.py strips each comma-separated segment, so a
        # whitespace-only value is equivalent to unset there too).
        ALLOWED_CIDRS="$(trim_whitespace "${env_vars[MAGPIE_ALLOWED_CIDRS]:-}")"
    fi
}

# =============================================================================
# Configuration gathering (interactive)
# =============================================================================

gather_config() {
    log "Gathering configuration..."

    # Install directory
    if [[ -z "$INSTALL_DIR" ]]; then
        prompt_value "Installation directory" "$DEFAULT_INSTALL_DIR" INSTALL_DIR
    fi

    # Data directory
    if [[ -z "$DATA_DIR" ]]; then
        DATA_DIR="${INSTALL_DIR}/data"
        prompt_value "Data storage directory" "$DATA_DIR" DATA_DIR
    fi

    # HTTP port
    if [[ -z "$HTTP_PORT" ]]; then
        HTTP_PORT="$DEFAULT_HTTP_PORT"
    fi

    # Trusted proxies (X-Forwarded-For). The bundled image serves plain
    # HTTP only and never terminates TLS itself (see issue #309's
    # 2026-07-18 decision) -- every deployment is expected to sit behind an
    # external TLS-terminating reverse proxy, so this is always relevant
    # (unlike the pre-0.2.0 two-container topology, where it only mattered
    # for a --tls-mode off install). See issue #575.
    if [[ -z "$TRUSTED_PROXIES" ]]; then
        prompt_value "Trusted proxy IP(s) (space-separated; the exact upstream reverse proxy in front of magpie, if any -- leave blank to trust none)" "$DEFAULT_TRUSTED_PROXIES" TRUSTED_PROXIES
    fi
}

# =============================================================================
# File generation
# =============================================================================

generate_env_file() {
    log "Generating environment configuration..."

    cat > "${INSTALL_DIR}/etc/.env" << EOF
# Magpie configuration
# Generated by magpie-deploy.sh v${MAGPIE_VERSION} on $(date -Iseconds)
# Re-run 'magpie-deploy.sh install --force' to regenerate

# Docker Compose settings
MAGPIE_DATA_DIR=${DATA_DIR}
MAGPIE_HTTP_PORT=${HTTP_PORT}
# Host IP the published port binds to. Empty/unset = all interfaces
# (0.0.0.0). See --bind-ip.
MAGPIE_BIND_IP=${BIND_IP:-}
# Pinned to the transitional -bundled image tag (a single container
# running Caddy + uvicorn -- see issue #309's 2026-07-18 decision). The
# canonical docker-compose.yml has no default for this key that resolves
# to a real, installer-compatible image, so it must always be set here.
MAGPIE_IMAGE=${GHCR_IMAGE}:${MAGPIE_VERSION}-bundled

# Server settings
MAGPIE_LOG_FORMAT=json
MAGPIE_RETENTION_DAYS=90

# Admin bootstrap token delivery -- REQUIRED by the canonical compose file
# (fail-closed, no Compose-level default there by design; an unset value
# here means the container exits non-zero at boot). 'file' writes a
# root-only file at MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH inside the
# bind-mounted data directory, readable on the host at
# ${DATA_DIR}/admin-token. See docs/installation.md "Admin Token Delivery"
# for the exec/discard/stdout alternatives.
MAGPIE_ADMIN_TOKEN_SINK=${DEFAULT_ADMIN_TOKEN_SINK}
MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=${DEFAULT_ADMIN_TOKEN_SINK_FILE_PATH}

# Trusted proxy IPs/CIDRs whose X-Forwarded-For header the bundled Caddy
# honors when determining the client IP used by MAGPIE_ALLOWED_CIDRS.
# Default: empty (trust no proxy; the real connecting peer's IP is used).
# Only set this to the exact upstream hop(s) -- never a broad range. See
# issue #575.
MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES:-}
EOF
}

generate_systemd_service() {
    log "Generating systemd service unit..."

    cat > /etc/systemd/system/magpie.service << EOF
# Magpie Artifact Storage Service
# Generated by magpie-deploy.sh v${MAGPIE_VERSION}

[Unit]
Description=Magpie Artifact Storage
Documentation=https://github.com/${GITHUB_REPO}
After=network-online.target docker.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/etc/.env

# -f pins the canonical operator file explicitly -- docker-compose.override.yml
# (the dev-only from-source build overlay) is never copied into INSTALL_DIR
# by clone_repo()/cmd_update(), but pinning here is belt-and-suspenders
# against a bare `docker compose` auto-merging one if it ever showed up.
ExecStart=/usr/bin/docker compose -f ${INSTALL_DIR}/docker-compose.yml --env-file ${INSTALL_DIR}/etc/.env up --no-build
# --remove-orphans: on a 2->1 topology swap (a v0.1.x install's leftover
# 'caddy' container), the old sidecar has no matching service in the
# current docker-compose.yml and would otherwise be left running,
# orphaned, after this stops the 'magpie' service. See issue #232.
ExecStop=/usr/bin/docker compose -f ${INSTALL_DIR}/docker-compose.yml --env-file ${INSTALL_DIR}/etc/.env down --remove-orphans

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal
SyslogIdentifier=magpie

[Install]
WantedBy=multi-user.target
EOF
}

generate_gc_units() {
    log "Generating GC timer units..."

    cat > /etc/systemd/system/magpie-gc.service << EOF
# Magpie Garbage Collection Service
# Generated by magpie-deploy.sh v${MAGPIE_VERSION}

[Unit]
Description=Magpie Garbage Collection
Documentation=https://github.com/${GITHUB_REPO}
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=${INSTALL_DIR}

# Use flock to prevent concurrent runs. If GC is already running, flock exits
# immediately. Unlike ConditionPathExists, flock automatically handles stale
# lock files from crashed processes.
ExecStart=/usr/bin/flock -n /var/run/magpie-gc.lock /usr/bin/docker compose -f ${INSTALL_DIR}/docker-compose.yml --env-file ${INSTALL_DIR}/etc/.env run --rm -T magpie magpie-ctl gc --quiet

StandardOutput=journal
StandardError=journal
SyslogIdentifier=magpie-gc

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/run

TimeoutStartSec=3600
EOF

    cat > /etc/systemd/system/magpie-gc.timer << 'EOF'
# Magpie Garbage Collection Timer
# Generated by magpie-deploy.sh

[Unit]
Description=Magpie Garbage Collection Timer
Documentation=https://github.com/SouthwestCCDC/magpie

[Timer]
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=1800
Persistent=true
AccuracySec=60

[Install]
WantedBy=timers.target
EOF
}

# =============================================================================
# Repository and image handling
# =============================================================================

# Extracts the version string from pyproject.toml content on stdin
# (format: `version = "X.Y.Z"`). Shared by detect_version() (reading the
# cloned file) and check_requested_image_exists() (reading pyproject.toml
# fetched directly from GitHub, before any clone happens). See issue #559.
extract_pyproject_version() {
    sed -nE 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' | head -n1
}

detect_version() {
    # Extract version from pyproject.toml
    # This function should be called after clone_repo() to ensure the repo exists
    local pyproject="${INSTALL_DIR}/repo/pyproject.toml"

    if [[ ! -f "$pyproject" ]]; then
        die "Cannot detect version: pyproject.toml not found at $pyproject"
    fi

    MAGPIE_VERSION=$(extract_pyproject_version < "$pyproject")

    if [[ -z "$MAGPIE_VERSION" ]]; then
        die "Failed to extract version from $pyproject"
    fi

    log "Detected magpie version: ${MAGPIE_VERSION}"
}

# Checks whether an image tag exists on ghcr.io, using the registry v2 HTTP
# API directly rather than `docker manifest inspect` (which requires Docker
# CLI experimental features enabled on many installs). GHCR issues
# anonymous pull tokens for public images without credentials.
#
# Return codes: 0 = confirmed present (HTTP 200), 1 = confirmed absent
# (HTTP 404 -- the only status treated as definitive), 2 = undetermined
# (no auth token, or any other non-200/404 status such as a rate limit or
# a transient 5xx) -- callers must treat 2 as "not verified", not "does
# not exist"; only 1 should ever block an install. See issue #559.
#
# Every `curl`/pipeline result here is captured via `... || true` (or, for
# the second curl, deliberate use of %{http_code} instead of -f) rather
# than left as a function's bare final/first-line command: under this
# script's `set -euo pipefail`, an unguarded nonzero exit from either would
# abort the entire installer immediately -- silently, with no die()
# message -- rather than letting this function return a code for its
# caller to handle. See issue #559.
ghcr_image_exists() {
    local image_tag="$1"
    local image_path="${image_tag%%:*}"
    local tag="${image_tag##*:}"
    local repo_path="${image_path#ghcr.io/}"

    local token
    token=$(curl -fsSL "https://ghcr.io/token?scope=repository:${repo_path}:pull" 2>/dev/null \
        | sed -nE 's/.*"token"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p') || true
    if [[ -z "$token" ]]; then
        return 2
    fi

    local http_code
    http_code=$(curl -sS -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer ${token}" \
        -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json" \
        "https://ghcr.io/v2/${repo_path}/manifests/${tag}" 2>/dev/null) || http_code=""

    case "$http_code" in
        200) return 0 ;;
        404) return 1 ;;
        *) return 2 ;;
    esac
}

# Pre-validates that a GHCR image is published for the requested release,
# fetching pyproject.toml directly from GitHub's raw content host (not via
# a full clone) so an unpublished image is caught before any on-disk
# change (mkdir, clone_repo, generated config files) is made. Only called
# for an explicitly requested release/ref -- see resolve_and_validate_release().
# Skipped for --from-source installs, which never pull a GHCR image. Only
# a confirmed-absent (404) image blocks the install; any inconclusive
# result (rate limit, transient error, no auth token) warns and lets the
# eventual `docker pull` be the real gate. See issue #559.
check_requested_image_exists() {
    if [[ "$FROM_SOURCE" == "true" ]]; then
        return 0
    fi

    # raw.githubusercontent.com/<owner>/<repo>/<ref>/<path> can't
    # disambiguate a ref containing '/' (e.g. a branch named "release/1.x",
    # which is_valid_git_ref() permits) from a path segment boundary, so a
    # slashed ref can't be looked up this way. This only affects slashed
    # branch names -- release tags (the primary use case, e.g. v0.1.4)
    # never contain '/'. Skip the pre-check rather than mis-resolving the
    # URL; the existing clone + pull_or_build_image() flow still validates
    # the image. See issue #559.
    if [[ "$GITHUB_BRANCH" == */* ]]; then
        log_warn "Skipping image pre-check for ref containing '/': ${GITHUB_BRANCH} (will be validated when the image is pulled)"
        return 0
    fi

    log "Checking that a magpie image is published for ${GITHUB_BRANCH}..."
    local pyproject_url="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}/pyproject.toml"
    local remote_version
    # Best-effort, like the manifest check below: an unreachable
    # raw.githubusercontent.com or an unparseable response is a transient
    # condition, not evidence the version is missing. Warn and skip the
    # image pre-check entirely rather than blocking the install -- the
    # eventual clone (which detects the version locally from the cloned
    # pyproject.toml) and docker pull remain the real gates. See #559.
    if ! remote_version=$(curl -fsSL "$pyproject_url" 2>/dev/null | extract_pyproject_version) || [[ -z "$remote_version" ]]; then
        log_warn "Could not determine the magpie version for '${GITHUB_BRANCH}' from ${pyproject_url}; skipping the image pre-check -- 'docker pull' will fail clearly later if the image is actually missing."
        return 0
    fi

    # The transitional -bundled tag (issue #594) is the only image this
    # installer runs -- see generate_env_file()/pull_or_build_image(). The
    # primary <version> tag (still the two-container topology) is not
    # installer-compatible; checking it here would pass while the actual
    # `docker pull` of the -bundled tag below still fails.
    local image_tag="${GHCR_IMAGE}:${remote_version}-bundled"
    local rc
    if ghcr_image_exists "$image_tag"; then
        rc=0
    else
        rc=$?
    fi

    if [[ $rc -eq 0 ]]; then
        log "Found image: ${image_tag}"
    elif [[ $rc -eq 1 ]]; then
        die "No published image found for version '${remote_version}' (ref ${GITHUB_BRANCH}): ${image_tag} does not exist on ghcr.io (confirmed absent). Refusing to install."
    else
        log_warn "Could not confirm image ${image_tag} exists (registry check was inconclusive); continuing -- 'docker pull' will fail clearly later if it's actually missing."
    fi
}

resolve_and_validate_release() {
    # Priority: --release flag (already in REQUESTED_RELEASE) > MAGPIE_VERSION
    # / GITHUB_REF env override (captured at script start, before the
    # Constants section repurposed the MAGPIE_VERSION name) > the default
    # branch. See issue #559.
    local from_cli="true"
    if [[ -z "$REQUESTED_RELEASE" ]]; then
        REQUESTED_RELEASE="$REQUESTED_RELEASE_ENV"
        from_cli="false"
    fi

    if [[ -z "$REQUESTED_RELEASE" ]]; then
        return 0
    fi

    # Strip common CI-style ref prefixes (e.g. a raw GITHUB_REF of
    # "refs/tags/v0.1.3"); only tag and branch names are meaningful to
    # `git clone --branch`. A value still in another ref namespace after
    # stripping (e.g. "refs/pull/123/merge") is rejected rather than
    # passed through -- is_valid_git_ref()'s charset allowlist alone
    # doesn't exclude "refs/..." paths, since '/' is a valid ref
    # character. See issue #559.
    REQUESTED_RELEASE="${REQUESTED_RELEASE#refs/tags/}"
    REQUESTED_RELEASE="${REQUESTED_RELEASE#refs/heads/}"
    if [[ "$REQUESTED_RELEASE" == refs/* ]]; then
        if [[ "$from_cli" == "true" ]]; then
            die "Invalid --release value: unsupported ref namespace '$REQUESTED_RELEASE' (only tags and branches are supported)"
        fi
        log_warn "Ignoring release env override in an unsupported ref namespace: $REQUESTED_RELEASE_ENV (only tags and branches are supported)"
        REQUESTED_RELEASE=""
        return 0
    fi

    if ! is_valid_git_ref "$REQUESTED_RELEASE"; then
        die "Invalid --release value: $REQUESTED_RELEASE (must start and end with a letter or digit; only letters, digits, '.', '_', '-', '/' are allowed elsewhere; must not contain '..' or '//')"
    fi

    GITHUB_BRANCH="$REQUESTED_RELEASE"

    log "Verifying requested release exists: ${GITHUB_BRANCH}..."
    local repo_url="https://github.com/${GITHUB_REPO}.git"

    # `git ls-remote --exit-code` distinguishes "genuinely no matching ref"
    # (exit 2) from any other failure (network, DNS, auth, repo access --
    # typically exit 128, e.g. "Could not resolve host" or "Repository not
    # found"). Only exit 2 is treated as definitive; every other failure is
    # best-effort -- warn and continue, letting the eventual `git clone`
    # (which hits the same remote) be the real gate. See the class of bug
    # this guards against generally: this pre-check may only hard-block on
    # a confirmed negative (this exit-2 case, or a confirmed-404 image in
    # check_requested_image_exists()); every other failure mode along this
    # path must not turn a transient condition into a false block. See #559.
    #
    # Constrained to --heads --tags: an unqualified `ls-remote <pattern>`
    # matches any ref, including special ones like `HEAD`, so `--release
    # HEAD` would otherwise pass this check even though `git clone --branch
    # HEAD` doesn't behave as a normal branch/tag checkout, and is_valid_git_ref()
    # has no reason to reject the literal string "HEAD" (it's a
    # syntactically ordinary, alphanumeric ref name). Restricting to heads
    # and tags means only an actual branch or tag counts as "found". See #559.
    #
    # Captured via a plain if/else (NOT `if ! cmd; then ... $? ...`): `!`
    # negates the exit status that `$?` reports afterwards too (`! false`
    # leaves `$?` at 0, not false's original 1), so a negated condition
    # can't be used to recover the original code -- only whether it was
    # zero. stderr is captured (via `2>&1 >/dev/null`, stdout discarded)
    # so a transport-failure message can be surfaced. See the same set -e
    # / error-code-granularity reasoning in ghcr_image_exists(). See #559.
    local ls_remote_err
    local ls_remote_rc
    if ls_remote_err=$(git ls-remote --exit-code --heads --tags "$repo_url" "$GITHUB_BRANCH" 2>&1 >/dev/null); then
        ls_remote_rc=0
    else
        ls_remote_rc=$?
    fi

    if [[ $ls_remote_rc -eq 2 ]]; then
        die "Requested release/tag '${GITHUB_BRANCH}' not found in ${GITHUB_REPO} (checked branches and tags)."
    elif [[ $ls_remote_rc -ne 0 ]]; then
        log_warn "Could not verify release/tag '${GITHUB_BRANCH}' against ${GITHUB_REPO} (inconclusive: ${ls_remote_err}); continuing -- 'git clone' will fail clearly later if it's actually missing."
    else
        log "Release ${GITHUB_BRANCH} found"
    fi

    check_requested_image_exists
}

update_repo_to_latest() {
    # Update repository to latest code from remote branch
    # Expects repo to already exist at ${INSTALL_DIR}/repo

    # `install --release <tag>` clones with `--depth 1 --branch <tag>`. When
    # <tag> is an actual tag (not a branch), git's implied --single-branch
    # scopes remote.origin.fetch to that tag alone and never creates an
    # origin/<branch> remote-tracking ref, so `reset --hard origin/$GITHUB_BRANCH`
    # below would fail with "unknown revision". Explicitly (re)scoping the
    # tracked branch here makes origin/$GITHUB_BRANCH resolvable regardless of
    # how the repo was originally cloned. Uses the non-additive form so
    # repeated `update` runs don't accumulate duplicate refspec entries in
    # .git/config; a normal branch clone already tracks $GITHUB_BRANCH, so
    # this is a no-op there. See issue #582.
    if ! git -C "${INSTALL_DIR}/repo" remote set-branches origin "$GITHUB_BRANCH"; then
        die "Failed to configure remote tracking for branch $GITHUB_BRANCH"
    fi
    if ! git -C "${INSTALL_DIR}/repo" fetch --depth 1 origin "$GITHUB_BRANCH"; then
        die "Failed to fetch latest repository code"
    fi
    if ! git -C "${INSTALL_DIR}/repo" reset --hard "origin/$GITHUB_BRANCH"; then
        die "Failed to reset repository to latest code"
    fi
}

clone_repo() {
    log "Cloning magpie repository..."

    local repo_url="https://github.com/${GITHUB_REPO}.git"
    local repo_dir="${INSTALL_DIR}/repo"

    # Remove existing repo if present
    rm -rf "$repo_dir"

    # Shallow clone for speed
    if ! git clone --depth 1 --branch "$GITHUB_BRANCH" "$repo_url" "$repo_dir"; then
        die "Failed to clone repository from $repo_url"
    fi

    # Copy only the canonical operator compose file. docker-compose.override.yml
    # (the dev-only from-source build overlay that a bare `docker compose up`
    # auto-merges inside a repo checkout) must never reach a real
    # deployment -- every docker-compose invocation in this script pins
    # -f docker-compose.yml as defense in depth against that. See
    # docker-compose.yml's own header comment.
    cp "${repo_dir}/docker-compose.yml" "${INSTALL_DIR}/"

    log "Repository cloned successfully"
}

# Idempotently sets KEY=value in a KEY=value .env file: replaces the
# existing line in place if present (anchored on ^KEY=, so a line that
# merely contains "KEY=" elsewhere in its value is never touched), or
# appends a new KEY=value line if absent. Every value reaching this
# function has already passed this script's own charset validators
# (is_valid_ip_or_cidr_list, validate_path_value) or is one of this
# script's own fixed literals (an image ref built from GHCR_IMAGE/
# MAGPIE_VERSION, "file", a fixed path) -- none can contain the `|` sed
# delimiter used below, `&`, or a backslash, so none can escape the
# replacement text. See issue #448's sed-injection precedent and issue
# #583 (the bug this helper fixes: cmd_update previously only ever wrote
# MAGPIE_TRUSTED_PROXIES when the key was absent).
upsert_env_key() {
    local env_file="$1"
    local key="$2"
    local value="$3"

    if grep -q "^${key}=" "$env_file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

# Removes a KEY= line from a .env file if present (anchored the same way as
# upsert_env_key() above). No-op if the key isn't there. Used by cmd_update
# to drop keys the canonical compose file no longer reads.
strip_env_key() {
    local env_file="$1"
    local key="$2"

    if grep -q "^${key}=" "$env_file" 2>/dev/null; then
        log "Removing ${key} from ${env_file} (no longer used)"
        sed -i "/^${key}=/d" "$env_file"
    fi
}

# cmd_update's .env surgery: `update` never regenerates .env wholesale
# (only `install` calls generate_env_file()) -- this edits the operator's
# persisted file in place. Reads the globals TRUSTED_PROXIES, BIND_IP, and
# TRUSTED_PROXIES_FROM_CLI (already resolved by load_existing_config()/
# parse_args()/the trusted-proxies gate, all of which cmd_update calls
# before this). Factored out of cmd_update() so it's independently
# testable without needing a real git repo or Docker (see
# scripts/test_installer_v020.sh).
reconcile_env_file_for_update() {
    local env_file="$1"

    # Strip keys the canonical compose file no longer reads (the pre-0.2.0
    # two-container topology's TLS configuration).
    strip_env_key "$env_file" "MAGPIE_DOMAIN"
    strip_env_key "$env_file" "MAGPIE_HTTPS_PORT"
    strip_env_key "$env_file" "TLS_MODE"
    strip_env_key "$env_file" "ACME_SERVER"
    strip_env_key "$env_file" "TLS_CERT"
    strip_env_key "$env_file" "TLS_KEY"

    # Add the fail-closed admin-token-sink keys if this .env predates them
    # (issue #387/#595) -- never clobber an operator's existing exec/discard
    # choice; only add what's missing.
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK=' "$env_file" 2>/dev/null; then
        log "Adding MAGPIE_ADMIN_TOKEN_SINK=${DEFAULT_ADMIN_TOKEN_SINK} to ${env_file} (required by the canonical compose file; not previously set)"
        upsert_env_key "$env_file" "MAGPIE_ADMIN_TOKEN_SINK" "$DEFAULT_ADMIN_TOKEN_SINK"
    fi
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=' "$env_file" 2>/dev/null; then
        upsert_env_key "$env_file" "MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH" "$DEFAULT_ADMIN_TOKEN_SINK_FILE_PATH"
    fi

    # Migrate legacy unprefixed BIND_IP (issue #446) forward to
    # MAGPIE_BIND_IP now that the canonical compose reads the prefixed key
    # (see docker-compose.yml's ports line); also covers a fresh
    # --bind-ip override passed on this run.
    upsert_env_key "$env_file" "MAGPIE_BIND_IP" "${BIND_IP:-}"
    if grep -q '^BIND_IP=' "$env_file" 2>/dev/null; then
        log "Removing stale legacy BIND_IP key from ${env_file} (superseded by MAGPIE_BIND_IP)"
        sed -i '/^BIND_IP=/d' "$env_file"
    fi

    # #583: an explicit --trusted-proxies on this run overrides an
    # already-persisted value in place; otherwise fall back to the
    # existing migrate-legacy-key-if-absent behavior.
    if [[ "$TRUSTED_PROXIES_FROM_CLI" == "true" ]]; then
        log "Setting MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES} in ${env_file} (--trusted-proxies)"
        upsert_env_key "$env_file" "MAGPIE_TRUSTED_PROXIES" "$TRUSTED_PROXIES"
    elif ! grep -q '^MAGPIE_TRUSTED_PROXIES=' "$env_file" 2>/dev/null; then
        log "Persisting MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES} to ${env_file} (migrated from legacy TRUSTED_PROXIES key)"
        upsert_env_key "$env_file" "MAGPIE_TRUSTED_PROXIES" "$TRUSTED_PROXIES"
    fi

    # Drop the stale legacy unprefixed TRUSTED_PROXIES= key now that its
    # value is guaranteed to be carried forward in MAGPIE_TRUSTED_PROXIES
    # (either already present above, or just written by the block above)
    # -- it's never read by docker-compose, so leaving it in place is
    # harmless but confusing clutter for an operator hand-inspecting .env.
    # Gated on MAGPIE_TRUSTED_PROXIES actually being present so the value
    # is never dropped without first being carried forward. Anchored on the
    # key at line start (^TRUSTED_PROXIES=, which does not match
    # ^MAGPIE_TRUSTED_PROXIES=) so this never touches an unrelated line
    # that merely contains the substring.
    if grep -q '^MAGPIE_TRUSTED_PROXIES=' "$env_file" 2>/dev/null \
        && grep -q '^TRUSTED_PROXIES=' "$env_file" 2>/dev/null; then
        log "Removing stale legacy TRUSTED_PROXIES key from ${env_file} (superseded by MAGPIE_TRUSTED_PROXIES)"
        sed -i '/^TRUSTED_PROXIES=/d' "$env_file"
    fi
}

pull_or_build_image() {
    local env_file="${INSTALL_DIR}/etc/.env"

    if [[ "$FROM_SOURCE" == "true" ]]; then
        log "Building magpie Docker image from source (this may take a few minutes)..."
        if ! docker build --pull -f "${INSTALL_DIR}/repo/Dockerfile.bundled" -t magpie:local "${INSTALL_DIR}/repo"; then
            die "Failed to build magpie image"
        fi
        upsert_env_key "$env_file" "MAGPIE_IMAGE" "magpie:local"
        log "Image built successfully"
    else
        local image_tag="${GHCR_IMAGE}:${MAGPIE_VERSION}-bundled"
        log "Pulling magpie image from container registry..."
        log "  Image: ${image_tag}"
        if ! docker pull "$image_tag"; then
            die "Failed to pull magpie image from ${image_tag}\nThe image tag is derived from the version in the cloned repo's pyproject.toml (${MAGPIE_VERSION}). If you used --release, confirm a release was published for that version."
        fi
        upsert_env_key "$env_file" "MAGPIE_IMAGE" "$image_tag"
        log "Image pulled successfully"
    fi
}

# =============================================================================
# Service management
# =============================================================================

pull_images() {
    log "Pulling Docker images..."
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" pull
}

start_services() {
    log "Starting services..."
    systemctl daemon-reload
    systemctl enable magpie.service
    systemctl enable magpie-gc.timer
    systemctl start magpie.service
    systemctl start magpie-gc.timer
}

wait_for_healthy() {
    log "Waiting for services to be healthy..."

    local max_attempts=30
    local attempt=1
    local health_url="http://127.0.0.1:${HTTP_PORT}/health"

    while (( attempt <= max_attempts )); do
        if curl -sf "$health_url" >/dev/null 2>&1; then
            log "Services are healthy"
            return 0
        fi
        echo -n "."
        sleep 2
        ((attempt++))
    done

    echo ""
    log_warn "Services may not be fully healthy. Check 'magpie logs' for details."
}

run_init() {
    log "Initializing database..."

    # Retry logic for container exec (container may need time to start)
    local max_retries=5
    local retry_delay=5
    local attempt=1
    local output
    local init_success=false

    while (( attempt <= max_retries )); do
        log "Attempting database initialization (attempt $attempt/$max_retries)..."

        # MAGPIE_ADMIN_TOKEN_SINK=stdout is overridden for just this exec so
        # the token can be scraped below and shown to the operator running
        # this installer, regardless of the deployed docker-compose.yml's
        # own default -- this is an operator-initiated `docker compose exec`
        # printing to this interactive install session, not the automatic
        # first-boot init that runs as the container's PID 1 (which is the
        # actual leak vector #387 addresses; see docs/installation.md).
        if output=$(docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" exec -T -e MAGPIE_ADMIN_TOKEN_SINK=stdout magpie magpie-ctl init 2>&1); then
            init_success=true
            break
        fi

        # Check if it's a "container not running" type error vs actual init failure
        if echo "$output" | grep -qiE "(already initialized|admin token already exists)"; then
            # Not an error - database was already initialized
            log "Database already initialized"
            return 0
        fi

        if echo "$output" | grep -qiE "(no container|not running|is not running)"; then
            log_warn "Container not ready, waiting ${retry_delay}s before retry..."
            sleep "$retry_delay"
            ((attempt++))
        else
            # Some other error - might be transient, retry anyway
            log_warn "Init attempt failed: $output"
            sleep "$retry_delay"
            ((attempt++))
        fi
    done

    if [[ "$init_success" != "true" ]]; then
        die "Failed to initialize database after $max_retries attempts.\nLast error: $output\nCheck container logs with: $SCRIPT_NAME logs"
    fi

    # Extract admin token from output
    local token
    token=$(echo "$output" | grep -Eo 'mgp_[[:alnum:]]+' || true)

    if [[ -n "$token" ]]; then
        echo ""
        echo "=============================================="
        echo "  IMPORTANT: Save your admin token!"
        echo "=============================================="
        echo ""
        echo "  Admin token: $token"
        echo ""
        echo "  This token will not be shown again."
        echo "  Store it securely for administrative access."
        echo "=============================================="
        echo ""
    else
        # No token in this exec's output -- almost always because the
        # container's own first-boot init (wrapper.sh's root prelude,
        # running before this script's health check returns) already
        # generated and delivered it via MAGPIE_ADMIN_TOKEN_SINK (default:
        # file, at ${DATA_DIR}/admin-token) before this exec ever ran.
        log "Database already initialized; admin token was delivered on first boot."
        log "Default sink is 'file': sudo cat ${DATA_DIR}/admin-token"
        log "To mint an additional admin-scope token instead:"
        log "  docker compose -f ${INSTALL_DIR}/docker-compose.yml --env-file ${INSTALL_DIR}/etc/.env exec magpie magpie-ctl token create --name ops-admin --scope admin"
    fi
}

# =============================================================================
# Commands
# =============================================================================

cmd_install() {
    log "Starting magpie installation..."

    check_prerequisites
    resolve_and_validate_release
    gather_config
    validate_config
    check_existing_installation

    # Carry forward MAGPIE_ALLOWED_CIDRS from an existing install's .env
    # when reinstalling with --force, purely so the trusted-proxies gate
    # below can still see it -- generate_env_file() always writes a fresh
    # .env from scratch below and does not otherwise consult the old one.
    if [[ -f "${INSTALL_DIR}/etc/.env" ]]; then
        local -A existing_env_vars=()
        read_env_file "${INSTALL_DIR}/etc/.env" existing_env_vars
        ALLOWED_CIDRS="$(trim_whitespace "${existing_env_vars[MAGPIE_ALLOWED_CIDRS]:-}")"
    fi
    warn_or_gate_trusted_proxies_for_cidr_allow "install"

    # Create directory structure
    log "Creating directory structure..."
    mkdir -p "${INSTALL_DIR}/etc"
    mkdir -p "${DATA_DIR}/artifacts"

    # Clone repo first (needed for Dockerfile.bundled on --from-source)
    clone_repo
    detect_version

    # Generate configuration files
    generate_env_file
    generate_systemd_service
    generate_gc_units

    # Pull or build the bundled image; sets MAGPIE_IMAGE in .env
    pull_or_build_image

    # Start everything
    start_services
    wait_for_healthy
    run_init

    echo ""
    log "Installation complete!"
    echo ""
    echo "  Installation directory: ${INSTALL_DIR}"
    echo "  Data directory: ${DATA_DIR}"
    # BIND_IP may bind the published port to a specific interface instead
    # of all of them (the default, 0.0.0.0) -- reflect the actual
    # configured bind rather than hardcoding the loopback address, which is
    # misleading (or simply wrong) once --bind-ip is anything else. See the
    # Copilot #1 finding on PR #597.
    if [[ -n "$BIND_IP" ]]; then
        echo "  Listening on: http://${BIND_IP}:${HTTP_PORT}"
    else
        echo "  Listening on: http://0.0.0.0:${HTTP_PORT} (all interfaces)"
    fi
    echo "  Plain HTTP only -- magpie does not terminate TLS; place a reverse"
    echo "  proxy in front of it for HTTPS."
    echo ""
    echo "  Manage with:"
    echo "    systemctl status magpie"
    echo "    $SCRIPT_NAME status"
    echo "    $SCRIPT_NAME logs -f"
    echo ""
}

# Interactive prompt for the issue #579 gate below: lets the operator either
# enter the upstream proxy's hop (sets TRUSTED_PROXIES and returns 0),
# confirm magpie is directly exposed (leaves TRUSTED_PROXIES empty and
# returns 0), or decline. Deliberately not built on confirm()/prompt_value():
# those honor --yes/NONINTERACTIVE defaults, which would let an unrelated
# --yes (e.g. for uninstall confirmations) silently answer this
# security-relevant question. Only called when NONINTERACTIVE is already
# known false.
#
# mode="update": a decline aborts via die() -- there's a running deployment
# to protect from an unattended lockout. mode="install": a decline only
# warns and proceeds -- a fresh install has nothing running yet to lock
# anyone out of, so there is no "not hard-fail" case to protect against.
prompt_trusted_proxies_for_cidr_allow() {
    local mode="$1"
    local response trimmed
    while true; do
        read -r -p "[magpie] Upstream proxy hop as seen by magpie's Caddy (e.g. 172.20.0.0/16), or leave blank if magpie is directly exposed: " response
        # trim_whitespace() makes a whitespace-only entry count as blank
        # too -- is_valid_ip_or_cidr_list tokenizes an all-whitespace
        # string to zero tokens and would otherwise accept it as a
        # trivially "valid" empty list, silently persisting an empty
        # MAGPIE_TRUSTED_PROXIES for what may have been a fat-fingered
        # proxy hop. Blank must always land on the explicit
        # direct-exposure confirmation below, never be silently accepted
        # here.
        trimmed="$(trim_whitespace "$response")"
        if [[ -z "$trimmed" ]]; then
            break
        fi
        if is_valid_ip_or_cidr_list "$trimmed"; then
            TRUSTED_PROXIES="$trimmed"
            log "Using MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES}."
            return 0
        fi
        log_error "Invalid entry: $response (expected a whitespace-separated list of IPv4/IPv6 addresses or CIDRs, e.g. 172.20.0.0/16). Try again, or leave blank if magpie is directly exposed."
    done

    read -r -p "[magpie] Confirm magpie is directly exposed to the internet with no reverse proxy in front of it [y/N]: " response
    case "$response" in
        [yY][eE][sS] | [yY])
            log "Proceeding with empty MAGPIE_TRUSTED_PROXIES (directly exposed, confirmed interactively)."
            return 0
            ;;
        *)
            if [[ "$mode" == "update" ]]; then
                die "Update aborted -- MAGPIE_TRUSTED_PROXIES was not confirmed. Re-run '$SCRIPT_NAME update' and either enter the proxy hop or confirm direct exposure."
            fi
            log_warn "Proceeding with empty MAGPIE_TRUSTED_PROXIES -- not confirmed as direct exposure. If magpie ends up behind a reverse proxy, set MAGPIE_TRUSTED_PROXIES in ${INSTALL_DIR}/etc/.env before relying on MAGPIE_ALLOWED_CIDRS."
            return 0
            ;;
    esac
}

# Fires when MAGPIE_ALLOWED_CIDRS is a real range and MAGPIE_TRUSTED_PROXIES
# resolves empty and was never deliberately configured. As of the bundled
# single-container image (v0.2.0, see issue #309's 2026-07-18 decision),
# magpie always serves plain HTTP and is always expected to sit behind an
# external TLS-terminating reverse proxy -- there is no longer a "Caddy
# faces the internet directly" mode to treat as lower-risk, so this gate now
# applies unconditionally rather than being tiered on the (removed)
# --tls-mode. See issue #579's original scope-expansion comment for that
# history.
#
# If MAGPIE_TRUSTED_PROXIES has never been configured for this install
# (TRUSTED_PROXIES_KEY_PRESENT is false -- load_existing_config() only sets
# it true for the new prefixed key with a real value or a truly zero-length
# "" (the exact deliberate-empty marker this script's own writeback
# produces), or a legacy unprefixed key with a real non-empty value, which
# is already migrated forward with a warning above) and the operator didn't
# pass --trusted-proxies or --accept-empty-trusted-proxies this run:
#
#   mode="update": GATE -- prompt interactively, or hard-fail via die() when
#     NONINTERACTIVE (unattended-lockout protection for a running
#     deployment).
#   mode="install": prompt interactively, or WARN (never die) when
#     NONINTERACTIVE -- a fresh install has no running service to lock
#     anyone out of.
#
# Fires once per install -- cmd_update's .env surgery persists whatever
# TRUSTED_PROXIES resolves to once the gate is satisfied, including an
# explicit empty value, so TRUSTED_PROXIES_KEY_PRESENT is true on every
# subsequent run.
warn_or_gate_trusted_proxies_for_cidr_allow() {
    local mode="$1"  # "install" or "update"

    # Canonical normalization point: by the time this function runs, every
    # source of TRUSTED_PROXIES -- the --trusted-proxies CLI flag
    # (parse_args, before this runs), .env (load_existing_config(), for
    # update), and gather_config()'s prompt (for install) -- has already
    # resolved into the global TRUSTED_PROXIES, and ALLOWED_CIDRS only ever
    # comes from .env. Re-trimming (idempotent) here, once, guarantees every
    # check below and every consumer later (validate_network_config, the
    # MAGPIE_TRUSTED_PROXIES writeback) sees a canonical, whitespace-safe
    # value regardless of which source produced it.
    TRUSTED_PROXIES="$(trim_whitespace "$TRUSTED_PROXIES")"
    ALLOWED_CIDRS="$(trim_whitespace "$ALLOWED_CIDRS")"

    # 255.255.255.255/32 is the bundled Caddyfile's own placeholder default
    # for an unset MAGPIE_ALLOWED_CIDRS -- never a real client, so treat it
    # the same as empty.
    local off_sentinel="255.255.255.255/32"

    [[ -z "$ALLOWED_CIDRS" || "$ALLOWED_CIDRS" == "$off_sentinel" ]] && return 0
    [[ -n "$TRUSTED_PROXIES" ]] && return 0
    if [[ "$TRUSTED_PROXIES_KEY_PRESENT" == "true" ]]; then
        # Already a deliberate, persisted choice (even if empty) -- silent.
        return 0
    fi
    if [[ "$ACCEPT_EMPTY_TRUSTED_PROXIES" == "true" ]]; then
        log "Proceeding with empty MAGPIE_TRUSTED_PROXIES (--accept-empty-trusted-proxies)."
        return 0
    fi

    local detected="MAGPIE_ALLOWED_CIDRS=${ALLOWED_CIDRS} is set for anonymous CIDR-based reads, but MAGPIE_TRUSTED_PROXIES has never been configured for this install. The bundled image serves plain HTTP and expects an external reverse proxy in front of it -- if the real client IP isn't forwarded correctly, every anonymous CIDR read will 401."
    local fix="Fix: set MAGPIE_TRUSTED_PROXIES to the upstream reverse proxy's hop as seen by magpie's Caddy -- commonly the docker bridge subnet, e.g. 172.20.0.0/16 (or the gateway /32) -- via --trusted-proxies <value> or by editing ${INSTALL_DIR}/etc/.env."
    local bypass="Bypass: if magpie is directly exposed (no reverse proxy), pass --accept-empty-trusted-proxies to proceed with an empty MAGPIE_TRUSTED_PROXIES."

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        if [[ "$mode" == "update" ]]; then
            log_error "$detected"
            log_error "$fix"
            die "$bypass"
        fi
        log_warn "$detected"
        log_warn "$fix"
        log_warn "$bypass"
        return 0
    fi

    log_warn "$detected"
    log_warn "$fix"
    log_warn "$bypass"
    prompt_trusted_proxies_for_cidr_allow "$mode"
}

cmd_update() {
    log "Updating magpie..."

    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

    # Unlike DATA_DIR, INSTALL_DIR is not persisted to .env -- it comes
    # straight from --install-dir on this invocation (or the default), and
    # is interpolated into every `-f ${INSTALL_DIR}/docker-compose.yml` /
    # `sed` call below, so an unvalidated value containing '|' or other
    # metacharacters could corrupt or hijack the on-disk files.
    # cmd_install validates this via validate_config(); cmd_update needs
    # its own check. See issue #448.
    local errors=()
    if [[ ! "$INSTALL_DIR" =~ ^/ ]]; then
        errors+=("Install directory must be an absolute path: $INSTALL_DIR")
    else
        validate_path_value "$INSTALL_DIR" "Install directory"
    fi
    if [[ ${#errors[@]} -gt 0 ]]; then
        log_error "Invalid --install-dir:"
        for err in "${errors[@]}"; do
            echo "  - $err" >&2
        done
        die "Fix the --install-dir value and try again."
    fi

    verify_installation
    load_existing_config

    # Tier-2 TLS deprecation gate: a persisted TLS_MODE of auto|manual means
    # this install used to terminate TLS itself (the pre-0.2.0 two-container
    # topology). The bundled image never does -- refuse to swap the running
    # deployment out from under an operator who hasn't acknowledged that
    # their TLS termination is going away. 'off' or absent means the
    # install was already HTTP-only; nothing changes, so no gate.
    if [[ "$PERSISTED_TLS_MODE" == "auto" || "$PERSISTED_TLS_MODE" == "manual" ]] \
        && [[ "$ACCEPT_BUILTIN_TLS_REMOVED" != "true" ]]; then
        log_error "This install's persisted TLS_MODE is '${PERSISTED_TLS_MODE}' -- it was terminating TLS itself."
        log_error "As of v0.2.0, magpie runs as a single bundled container that serves plain HTTP only (see issue #309's 2026-07-18 decision); it no longer terminates TLS."
        log_error "After this update, magpie will serve plain HTTP on MAGPIE_HTTP_PORT with no TLS. Place a reverse proxy in front of it for HTTPS before proceeding."
        die "Once your reverse proxy is in place, re-run with --accept-builtin-tls-removed to acknowledge and continue."
    fi

    warn_or_gate_trusted_proxies_for_cidr_allow "update"

    local env_file="${INSTALL_DIR}/etc/.env"

    # Re-validate TRUSTED_PROXIES/BIND_IP loaded from .env before they're
    # written back below -- guards against a hand-edited or stale .env
    # value that would no longer pass validation.
    local errors=()
    validate_network_config
    if [[ ${#errors[@]} -gt 0 ]]; then
        log_error "Existing configuration in .env failed validation:"
        for err in "${errors[@]}"; do
            echo "  - $err" >&2
        done
        die "Fix or remove the invalid value(s) in ${env_file}, or reinstall."
    fi

    # Runs before any git/docker work below -- fails fast on a config
    # problem rather than after an expensive fetch/pull.
    reconcile_env_file_for_update "$env_file"

    # Verify repo directory exists
    if [[ ! -d "${INSTALL_DIR}/repo" ]]; then
        die "Repository directory not found at ${INSTALL_DIR}/repo\nThe installation may be corrupted. Try reinstalling with 'install --force'."
    fi

    # Pull latest repo code to detect current version
    log "Pulling latest repository code to detect version..."
    update_repo_to_latest

    # Detect version from updated repo
    detect_version

    # Ensure repository is not shallow so tags can be fetched reliably
    if git -C "${INSTALL_DIR}/repo" rev-parse --is-shallow-repository >/dev/null 2>&1; then
        if [[ "$(git -C "${INSTALL_DIR}/repo" rev-parse --is-shallow-repository)" == "true" ]]; then
            log "Repository is shallow; fetching full history to access tags..."
            if ! git -C "${INSTALL_DIR}/repo" fetch --unshallow --tags; then
                die "Failed to unshallow repository to fetch tags"
            fi
        fi
    fi

    # Checkout the version tag for consistency when it exists.
    # If no corresponding tag is found (e.g. development version), stay on the branch HEAD.
    if git -C "${INSTALL_DIR}/repo" ls-remote --tags origin "v${MAGPIE_VERSION}" | grep -q .; then
        log "Checking out version tag v${MAGPIE_VERSION}..."
        if ! git -C "${INSTALL_DIR}/repo" fetch origin "refs/tags/v${MAGPIE_VERSION}:refs/tags/v${MAGPIE_VERSION}"; then
            die "Failed to fetch version tag v${MAGPIE_VERSION}"
        fi
        if ! git -C "${INSTALL_DIR}/repo" checkout "v${MAGPIE_VERSION}"; then
            die "Failed to checkout version tag v${MAGPIE_VERSION}"
        fi
    else
        log_warn "No git tag v${MAGPIE_VERSION} found for detected version; continuing on branch ${GITHUB_BRANCH}"
    fi

    # Update the canonical compose file from the repo. This is the only
    # file swapped wholesale on update -- docker-compose.override.yml is
    # never copied (dev-only), and .env was already reconciled above (not
    # regenerated -- only `install` calls generate_env_file()).
    log "Updating docker-compose.yml..."
    cp "${INSTALL_DIR}/repo/docker-compose.yml" "${INSTALL_DIR}/"

    if [[ "$FROM_SOURCE" == "true" ]]; then
        log "Rebuilding magpie image from source..."
        if ! docker build --pull -f "${INSTALL_DIR}/repo/Dockerfile.bundled" -t magpie:local "${INSTALL_DIR}/repo"; then
            die "Failed to rebuild magpie image"
        fi
        upsert_env_key "$env_file" "MAGPIE_IMAGE" "magpie:local"
    else
        local image_tag="${GHCR_IMAGE}:${MAGPIE_VERSION}-bundled"
        log "Pulling magpie image from container registry..."
        log "  Image: ${image_tag}"
        if ! docker pull "$image_tag"; then
            die "Failed to pull magpie image from ${image_tag}"
        fi
        # MAGPIE_IMAGE always reflects the version just resolved above.
        upsert_env_key "$env_file" "MAGPIE_IMAGE" "$image_tag"
    fi

    # --remove-orphans (baked into ExecStop, see generate_systemd_service())
    # drops a v0.1.x install's leftover 'caddy' sidecar container when the
    # restart below stops the old stack and starts the single-service one.
    # /data is a bind mount, untouched by the container swap.
    log "Restarting services..."
    systemctl restart magpie.service

    wait_for_healthy

    log "Update complete!"
    echo ""
    echo "  Updated to version: ${MAGPIE_VERSION}"
    echo "  Note: docker-compose.yml and .env have been updated from the repository."
    echo "  Any local customizations to docker-compose.yml have been overwritten; review and reapply as needed."
    echo ""
}

cmd_uninstall() {
    log "Uninstalling magpie..."

    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
    verify_installation
    load_existing_config

    if [[ "$PURGE" == "true" ]]; then
        # DATA_DIR was just loaded from .env above; re-validate it before
        # it's used in `rm -rf` -- a stale or hand-edited .env could
        # otherwise point --purge at an arbitrary path. See issue #448.
        local errors=()
        if [[ -z "$DATA_DIR" ]]; then
            errors+=("Data directory is empty; refusing to run --purge")
        elif [[ ! "$DATA_DIR" =~ ^/ ]]; then
            errors+=("Data directory must be an absolute path: $DATA_DIR")
        else
            validate_path_value "$DATA_DIR" "Data directory"
        fi
        if [[ ${#errors[@]} -gt 0 ]]; then
            log_error "Refusing to purge -- data directory failed validation:"
            for err in "${errors[@]}"; do
                echo "  - $err" >&2
            done
            die "Fix ${INSTALL_DIR}/etc/.env or reinstall, then retry."
        fi

        if ! confirm "This will permanently delete all artifacts and data. Continue?"; then
            log "Uninstall cancelled."
            exit 0
        fi
    else
        if ! confirm "This will stop and remove magpie (data will be preserved). Continue?"; then
            log "Uninstall cancelled."
            exit 0
        fi
    fi

    log "Stopping services..."
    systemctl stop magpie.service 2>/dev/null || true
    systemctl stop magpie-gc.timer 2>/dev/null || true
    systemctl disable magpie.service 2>/dev/null || true
    systemctl disable magpie-gc.timer 2>/dev/null || true

    # Absolute -f (not `cd "$INSTALL_DIR"` + a relative compose lookup) so
    # this doesn't depend on INSTALL_DIR still being a valid working
    # directory -- it's about to be rm -rf'd below. See issue #161.
    log "Removing containers and volumes..."
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" down --volumes 2>/dev/null || true

    log "Removing systemd units..."
    rm -f /etc/systemd/system/magpie.service
    rm -f /etc/systemd/system/magpie-gc.service
    rm -f /etc/systemd/system/magpie-gc.timer
    systemctl daemon-reload

    if [[ "$PURGE" == "true" ]]; then
        log "Removing data directory: ${DATA_DIR}"
        rm -rf "${DATA_DIR}"
    fi

    log "Removing installation directory: ${INSTALL_DIR}"
    rm -rf "${INSTALL_DIR}"

    log "Uninstall complete!"
    if [[ "$PURGE" != "true" ]]; then
        echo "  Data directory preserved at: ${DATA_DIR}"
        echo "  Use --purge to remove data as well."
        echo ""
        echo "  NOTE: If you reinstall with different settings (e.g., different UID/GID"
        echo "  in container configuration), you may encounter permission issues with"
        echo "  the preserved data directory. In that case, you may need to manually"
        echo "  adjust ownership: chown -R <new-uid>:<new-gid> ${DATA_DIR}"
    fi
}

cmd_status() {
    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
    verify_installation
    load_existing_config

    echo "=== Magpie Status ==="
    echo ""

    echo "--- Systemd Service ---"
    systemctl status magpie.service --no-pager 2>/dev/null || echo "  Service not running"
    echo ""

    echo "--- Container Status ---"
    # Absolute -f, not `cd "$INSTALL_DIR"`: a partially-removed or
    # inaccessible INSTALL_DIR would otherwise fail the cd itself before
    # reaching the "No containers" fallback below. See issue #161.
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" ps 2>/dev/null || echo "  No containers"
    echo ""

    echo "--- Health Check ---"
    local health_url="http://127.0.0.1:${HTTP_PORT:-8080}/health"
    if curl -sf "$health_url" >/dev/null 2>&1; then
        echo "  API: healthy ($health_url)"
    else
        echo "  API: unhealthy or unreachable ($health_url)"
    fi
    echo ""

    echo "--- GC Timer ---"
    systemctl status magpie-gc.timer --no-pager 2>/dev/null || echo "  Timer not active"
}

cmd_logs() {
    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
    verify_installation

    # Validate --lines before it reaches docker compose. See issue #448.
    if ! [[ "$LINES" =~ ^[0-9]+$ ]]; then
        die "Invalid --lines value: $LINES (must be a non-negative integer)"
    fi

    # Build the docker-compose argument list as a quoted array rather than
    # interpolating into an unquoted command line. See issue #448. Absolute
    # -f rather than `cd "$INSTALL_DIR"` first -- see issue #161.
    local compose_args=(-f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" logs)
    [[ "$FOLLOW" == "true" ]] && compose_args+=(-f)
    compose_args+=(--tail="$LINES")
    compose_args+=("$@")

    docker compose "${compose_args[@]}"
}

# =============================================================================
# Help and usage
# =============================================================================

show_help() {
    cat << EOF
Magpie Installer v${HARDCODED_VERSION}

Usage: $SCRIPT_NAME <command> [options]

Commands:
  install     Install magpie (fresh installation)
  update      Update to latest version
  uninstall   Remove magpie
  status      Show service status
  logs        View container logs

Global options:
  -h, --help              Show this help message and exit
  -v, --version           Show this installer script's own version and exit

Install options (only used with 'install' command):
  --release VERSION       Install a specific release/tag instead of the
                          default branch (e.g. --release v0.1.3). Validates
                          that the tag/ref exists, and best-effort checks
                          that its ghcr.io image exists; fails clearly only
                          when the tag or image is confirmed missing.
                          Env override: MAGPIE_VERSION or GITHUB_REF.
                          Default: $DEFAULT_GITHUB_BRANCH branch (latest)
  --install-dir PATH      Installation directory (default: $DEFAULT_INSTALL_DIR)
  --data-dir PATH         Data storage directory (default: INSTALL_DIR/data)
                          Must be an absolute path. Paths under /home are not
                          supported due to systemd hardening (ProtectHome=true).
                          Note: Symlinks to /home paths will also fail with GC service.
                          Recommended: /srv/magpie/data or /opt/magpie/data
  --http-port PORT        HTTP port (default: $DEFAULT_HTTP_PORT)
  --bind-ip IP            Bind the published port to a specific host IP
                          (default: all interfaces). Example: --bind-ip 10.3.3.107
  --trusted-proxies CIDR  Space-separated IPs/CIDRs whose X-Forwarded-For
                          header the bundled Caddy trusts (default: none --
                          the real connecting peer's IP is used). Only set
                          this to the exact upstream hop(s) if something
                          sits in front of magpie; never a broad range. See
                          issue #575. Also accepted by 'update' to satisfy
                          the issue #579 gate below, or to change an
                          already-persisted value in place (issue #583).
  --noninteractive        Skip interactive prompts (use defaults for all config)
  --force                 Overwrite existing installation
  --from-source           Build image from source instead of pulling from ghcr.io

Update options:
  --from-source                    Rebuild image from source instead of
                                    pulling from ghcr.io
  --trusted-proxies CIDR           See Install options above -- also
                                    applies to 'update', where it overrides
                                    an already-persisted value in place.
  --accept-empty-trusted-proxies   Acknowledge that an empty
                                    MAGPIE_TRUSTED_PROXIES is intentional
                                    (magpie is directly exposed, no reverse
                                    proxy). Only meaningful when 'update'
                                    finds a real MAGPIE_ALLOWED_CIDRS with
                                    no MAGPIE_TRUSTED_PROXIES ever
                                    configured for this install -- without
                                    it (or --trusted-proxies), that
                                    combination prompts interactively or,
                                    with --noninteractive, hard-fails the
                                    update. See issue #579.
  --accept-builtin-tls-removed     Acknowledge that this install's
                                    persisted TLS_MODE (auto or manual --
                                    it was terminating TLS itself in the
                                    pre-v0.2.0 two-container topology) no
                                    longer applies: the bundled image is
                                    always HTTP-only. Without it, 'update'
                                    refuses to proceed on such an install.
                                    Not needed for a persisted TLS_MODE of
                                    'off' (already HTTP-only) or an install
                                    that predates TLS_MODE entirely.

Uninstall options:
  --yes, -y               Skip confirmation prompts (auto-confirm uninstall)
  --purge                 Also remove data directory (requires --yes for non-interactive)
                          Without --purge: preserves data directory for reinstallation
                          With --purge: permanently deletes all artifacts and data

Logs options:
  -f, --follow            Follow log output
  -n, --lines N           Number of lines to show (default: 100)

Deprecated options (removed in v0.2.0's bundled image, kept as accepted
no-ops through v0.2.x for compatibility with existing scripts/playbooks --
will be REJECTED starting in v0.3.0):
  --tls-mode, --domain, --tls-cert, --tls-key, --https-port, --acme-server
                          The bundled image serves plain HTTP only and
                          never terminates TLS -- front it with your own
                          reverse proxy for HTTPS. Passing any of these
                          prints a deprecation warning and is otherwise
                          ignored.

Examples:
  # Interactive installation
  sudo $SCRIPT_NAME install

  # Non-interactive installation
  sudo $SCRIPT_NAME install --noninteractive

  # Installation with custom data directory and a trusted upstream proxy
  sudo $SCRIPT_NAME install --data-dir /srv/magpie/data --trusted-proxies 172.20.0.0/16

  # Install a specific tagged release instead of the default branch
  sudo $SCRIPT_NAME install --release v0.1.3

  # Update existing installation
  sudo $SCRIPT_NAME update

  # View logs
  sudo $SCRIPT_NAME logs -f

  # Uninstall (preserve data)
  sudo $SCRIPT_NAME uninstall

  # Uninstall completely
  sudo $SCRIPT_NAME uninstall --purge --yes

EOF
}

# =============================================================================
# Argument parsing
# =============================================================================

parse_args() {
    local command=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            install|update|uninstall|status|logs)
                command="$1"
                shift
                ;;
            --install-dir)
                INSTALL_DIR="$2"
                shift 2
                ;;
            --data-dir)
                DATA_DIR="$2"
                shift 2
                ;;
            # Tier 1 deprecation (v0.2.0, see docs/installation.md
            # "Deprecated in v0.2.0"): the bundled image has no in-container
            # TLS or Caddyfile to configure, so these six flags no longer
            # set anything -- they're still PARSED (consume their value and
            # `shift 2`) purely so an existing script/playbook that still
            # passes one doesn't hit "Unknown option" and abort. Removed
            # entirely in v0.3.0.
            --tls-mode)
                case "$2" in
                    off)
                        log_warn "--tls-mode is deprecated and ignored; the bundled image is HTTP-only. 'off' was already the effective behavior. Remove it; front magpie with your own TLS proxy. This flag will be removed in v0.3.0."
                        ;;
                    auto|manual)
                        log_warn "--tls-mode auto|manual is deprecated and IGNORED; the bundled image does not terminate TLS. Put an external reverse proxy in front of magpie for HTTPS. This flag will be removed in v0.3.0."
                        ;;
                    *)
                        log_warn "--tls-mode is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                        ;;
                esac
                shift 2
                ;;
            --domain)
                log_warn "--domain is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                shift 2
                ;;
            --tls-cert)
                log_warn "--tls-cert is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                shift 2
                ;;
            --tls-key)
                log_warn "--tls-key is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                shift 2
                ;;
            --https-port)
                log_warn "--https-port is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                shift 2
                ;;
            --acme-server)
                log_warn "--acme-server is deprecated and ignored (no in-container TLS). This flag will be removed in v0.3.0."
                shift 2
                ;;
            --http-port)
                HTTP_PORT="$2"
                shift 2
                ;;
            --trusted-proxies)
                TRUSTED_PROXIES="$2"
                # Marks this as a deliberate override for cmd_update's #583
                # fix: an explicit --trusted-proxies must update an
                # already-persisted MAGPIE_TRUSTED_PROXIES in place, not
                # just fill in an absent key.
                TRUSTED_PROXIES_FROM_CLI="true"
                shift 2
                ;;
            # --accept-<specific-thing> is this installer's convention for
            # acknowledging a SAFETY GATE (a check that stops a security- or
            # data-affecting mistake, e.g. the trusted-proxies lockout gate
            # in warn_or_gate_trusted_proxies_for_cidr_allow()) -- never a
            # blanket --force/--bypass-safety-checks, which would silently
            # swallow future gates too. See
            # .github/copilot-instructions.md for the full rationale. Add
            # new gates' acks the same way.
            --accept-empty-trusted-proxies)
                ACCEPT_EMPTY_TRUSTED_PROXIES="true"
                shift
                ;;
            --accept-builtin-tls-removed)
                ACCEPT_BUILTIN_TLS_REMOVED="true"
                shift
                ;;
            --bind-ip)
                BIND_IP="$2"
                # See BIND_IP_FROM_CLI's declaration -- mirrors
                # TRUSTED_PROXIES_FROM_CLI so load_existing_config() knows
                # not to clobber this with a stale .env value on update.
                BIND_IP_FROM_CLI="true"
                shift 2
                ;;
            --noninteractive)
                NONINTERACTIVE="true"
                shift
                ;;
            --force)
                FORCE="true"
                shift
                ;;
            --from-source)
                FROM_SOURCE="true"
                shift
                ;;
            --purge)
                PURGE="true"
                shift
                ;;
            --yes|-y)
                YES="true"
                shift
                ;;
            --follow|-f)
                FOLLOW="true"
                shift
                ;;
            --lines|-n)
                LINES="$2"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            --version|-v)
                echo "$SCRIPT_NAME v${HARDCODED_VERSION}"
                exit 0
                ;;
            --release)
                # Selects a release/tag to install (issue #559) -- a
                # plain option-with-value, distinct from --version/-v
                # above (which only ever prints this script's own
                # version and exits).
                #
                # Missing value (end of args, or the next token is
                # option-shaped, starting with '-') and an explicit empty
                # string both die rather than silently falling through:
                # an empty REQUESTED_RELEASE is indistinguishable from "no
                # release requested" in resolve_and_validate_release(),
                # which would otherwise silently install the default
                # branch instead of erroring. A value that happens to look
                # like a command name (e.g. --release status) needs no
                # special-casing -- it's just a value.
                if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
                    die "--release requires a value, e.g. --release v0.1.4"
                fi
                REQUESTED_RELEASE="$2"
                shift 2
                ;;
            -*)
                die "Unknown option: $1\nUse --help for usage information."
                ;;
            *)
                # Pass remaining args to command (e.g., service names for logs)
                break
                ;;
        esac
    done

    if [[ -z "$command" ]]; then
        show_help
        exit 1
    fi

    # --release selects a release to install and is only meaningful for
    # 'install' -- the env-var override (MAGPIE_VERSION/GITHUB_REF) is
    # deliberately not checked here, since it may be set in an operator's
    # environment for unrelated reasons and shouldn't break other commands.
    # See issue #559.
    if [[ -n "$REQUESTED_RELEASE" ]] && [[ "$command" != "install" ]]; then
        die "--release is only supported by the 'install' command (got: $command)"
    fi

    # Execute command
    case "$command" in
        install) cmd_install ;;
        update) cmd_update ;;
        uninstall) cmd_uninstall ;;
        status) cmd_status ;;
        logs) cmd_logs "$@" ;;
    esac
}

# =============================================================================
# Main
# =============================================================================

main() {
    if [[ $# -eq 0 ]]; then
        show_help
        exit 1
    fi

    parse_args "$@"
}

main "$@"

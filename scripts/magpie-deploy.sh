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
DEFAULT_TLS_MODE="off"
DEFAULT_HTTP_PORT="8080"
DEFAULT_HTTPS_PORT="8443"
# Trust no proxy by default -- Caddy uses the real connecting peer's IP.
# Only set (via --trusted-proxies or the interactive prompt for --tls-mode
# off) to the exact upstream hop(s) when Caddy sits behind another reverse
# proxy. A broad range here would let any client on it spoof
# X-Forwarded-For and defeat MAGPIE_ALLOWED_CIDRS. See issue #575.
DEFAULT_TRUSTED_PROXIES=""

# =============================================================================
# Global variables (populated during config)
# =============================================================================

INSTALL_DIR=""
DATA_DIR=""
TLS_MODE=""
DOMAIN=""
HTTP_PORT=""
HTTPS_PORT=""
TLS_CERT=""
TLS_KEY=""
TRUSTED_PROXIES=""
# Read-only mirror of MAGPIE_ALLOWED_CIDRS from an existing install's .env --
# this script never sets or persists it (docker-compose passes it straight
# through to the app); it's only loaded so cmd_update can warn when it's
# paired with an empty MAGPIE_TRUSTED_PROXIES. See issue #579.
ALLOWED_CIDRS=""
# Whether MAGPIE_TRUSTED_PROXIES (or the legacy unprefixed TRUSTED_PROXIES)
# key was present in the existing install's .env at the start of this run,
# before load_existing_config()'s migration/writeback touches anything.
# Presence -- even an explicit empty value -- is the deliberate-choice
# signal cmd_update's issue #579 gate uses to fire only once. See
# load_existing_config().
TRUSTED_PROXIES_KEY_PRESENT="false"
BIND_IP=""
ACME_SERVER=""
NONINTERACTIVE="false"
FORCE="false"
PURGE="false"
YES="false"
FOLLOW="false"
LINES="100"
FROM_SOURCE="false"
REQUESTED_RELEASE=""  # from --release; empty means "use the default branch"
# --accept-empty-trusted-proxies (update only): acknowledges that an empty
# MAGPIE_TRUSTED_PROXIES is intentional (magpie is directly exposed), so
# cmd_update's issue #579 gate proceeds instead of prompting/dying. See
# warn_or_gate_trusted_proxies_for_cidr_allow().
ACCEPT_EMPTY_TRUSTED_PROXIES="false"

# =============================================================================
# Helper functions
# =============================================================================

log() {
    echo "[magpie] $*"
}

log_error() {
    echo "[magpie] ERROR: $*" >&2
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

# Matches a syntactically valid DNS hostname: labels of alphanumerics and
# hyphens (not starting/ending with a hyphen), separated by dots. Excludes
# every character that would let a value act as regex, sed script, or shell
# metacharacters ('/', '\', '$', backtick, ';', '&', newline, etc.).
is_valid_domain() {
    local domain="$1"
    [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]
}

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

# Matches an https:// URL using only an allowlisted RFC 3986-ish charset
# for the authority and path. This is a strict ALLOWLIST (not a denylist of
# specific bytes like whitespace/braces): it excludes every character not
# explicitly permitted, including backslash. That matters because the
# value is later interpolated into an awk program via ENVIRON (not `-v`),
# but a denylist alone is not enough defense in depth -- `awk -v x=value`
# decodes backslash escapes (`\n`, octal `\173`/`\175`/`\040`, etc.) in the
# assigned value *before* the awk program runs, so a denylist that checks
# only for literal whitespace/braces can be bypassed with escape sequences
# that decode into them. Excluding backslash here closes that off
# independently of how the value is later consumed. See issue #448.
is_valid_acme_server_url() {
    local value="$1"
    local pattern='^https://[A-Za-z0-9.-]+(:([0-9]{1,5}))?(/[A-Za-z0-9._~%!$&()*+,;=:@/-]*)?$'
    [[ "$value" =~ $pattern ]] || return 1

    # The charset regex alone allows a syntactically-shaped but out-of-range
    # port (e.g. ":99999", which is 1-5 digits but > 65535) -- that would
    # pass validation and then produce a Caddyfile Caddy rejects at
    # install/update time. `10#...` forces base-10 (see the CIDR octet
    # comment above for why: a leading zero would otherwise be read as
    # octal by bash arithmetic).
    local port="${BASH_REMATCH[2]}"
    if [[ -n "$port" ]]; then
        (( 10#$port >= 1 && 10#$port <= 65535 )) || return 1
    fi
    return 0
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

# Validates DOMAIN, TRUSTED_PROXIES, BIND_IP, and ACME_SERVER format.
# Called both during install (via validate_config) and update (to
# re-validate values loaded from an existing .env before they're used to
# regenerate the Caddyfile). Appends to the caller's 'errors' array (relies
# on bash's dynamic scoping of locals). See issue #448.
validate_network_config() {
    if [[ -n "$DOMAIN" ]] && ! is_valid_domain "$DOMAIN"; then
        errors+=("Invalid domain: $DOMAIN (must be a valid hostname, e.g. magpie.example.com)")
    fi

    if [[ -n "$TRUSTED_PROXIES" ]] && ! is_valid_ip_or_cidr_list "$TRUSTED_PROXIES"; then
        errors+=("Invalid --trusted-proxies: $TRUSTED_PROXIES (must be a whitespace-separated list of IPv4/IPv6 addresses or CIDRs)")
    fi

    if [[ -n "$BIND_IP" ]]; then
        if [[ "$BIND_IP" == */* ]] || ! is_valid_ip_or_cidr "$BIND_IP"; then
            errors+=("Invalid --bind-ip: $BIND_IP (must be a single IPv4 or IPv6 address, no CIDR prefix)")
        fi
    fi

    if [[ -n "$ACME_SERVER" ]] && ! is_valid_acme_server_url "$ACME_SERVER"; then
        errors+=("Invalid --acme-server: $ACME_SERVER (must be an https:// URL using only RFC 3986 host/path characters; no whitespace, braces, or backslashes)")
    fi
}

validate_config() {
    local errors=()

    # TLS mode validation
    case "$TLS_MODE" in
        off|auto|manual) ;;
        *) errors+=("Invalid TLS mode: $TLS_MODE (must be off, auto, or manual)") ;;
    esac

    # Domain required for TLS
    if [[ "$TLS_MODE" != "off" ]] && [[ -z "$DOMAIN" ]]; then
        errors+=("Domain is required for TLS mode '$TLS_MODE'")
    fi

    # ACME server only valid with auto TLS mode
    if [[ -n "$ACME_SERVER" ]] && [[ "$TLS_MODE" != "auto" ]]; then
        errors+=("--acme-server can only be used with --tls-mode auto (current mode: $TLS_MODE)")
    fi

    # Certificate paths for manual TLS
    if [[ "$TLS_MODE" == "manual" ]]; then
        if [[ -z "$TLS_CERT" ]]; then
            errors+=("TLS certificate path required for manual TLS mode (--tls-cert)")
        elif [[ ! -f "$TLS_CERT" ]]; then
            errors+=("TLS certificate not found: $TLS_CERT")
        fi

        if [[ -z "$TLS_KEY" ]]; then
            errors+=("TLS key path required for manual TLS mode (--tls-key)")
        elif [[ ! -f "$TLS_KEY" ]]; then
            errors+=("TLS key not found: $TLS_KEY")
        fi
    fi

    # Port validation
    if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]] || (( HTTP_PORT < 1 || HTTP_PORT > 65535 )); then
        errors+=("Invalid HTTP port: $HTTP_PORT")
    fi

    if ! [[ "$HTTPS_PORT" =~ ^[0-9]+$ ]] || (( HTTPS_PORT < 1 || HTTPS_PORT > 65535 )); then
        errors+=("Invalid HTTPS port: $HTTPS_PORT")
    fi

    # Network value validation (DOMAIN, TRUSTED_PROXIES, BIND_IP, ACME_SERVER)
    # These values later flow into the generated .env and Caddyfile, so they
    # must be validated here, before any file is generated. See issue #448.
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

        # Map env vars to script variables. MAGPIE_* prefixed vars map to
        # their unprefixed script variable name (DATA_DIR, HTTP_PORT,
        # HTTPS_PORT, DOMAIN); the unprefixed persisted keys below
        # (TLS_MODE, BIND_IP, ACME_SERVER) map directly.
        DATA_DIR="${env_vars[MAGPIE_DATA_DIR]:-$DATA_DIR}"
        HTTP_PORT="${env_vars[MAGPIE_HTTP_PORT]:-$HTTP_PORT}"
        HTTPS_PORT="${env_vars[MAGPIE_HTTPS_PORT]:-$HTTPS_PORT}"
        DOMAIN="${env_vars[MAGPIE_DOMAIN]:-$DOMAIN}"
        TLS_MODE="${env_vars[TLS_MODE]:-$TLS_MODE}"
        BIND_IP="${env_vars[BIND_IP]:-$BIND_IP}"
        ACME_SERVER="${env_vars[ACME_SERVER]:-$ACME_SERVER}"

        # MAGPIE_TRUSTED_PROXIES (issue #575) replaces the pre-#575
        # unprefixed TRUSTED_PROXIES key. Prefer the new key; fall back to
        # migrating the old one so an existing installation's configured
        # value survives an `update` unchanged (never silently narrowed to
        # empty here -- that could break a fronted deployment relying on
        # it). If the migrated legacy value is exactly the old overly-broad
        # default this issue fixes, warn loudly rather than fix it
        # automatically: only the operator knows the actual upstream hop(s)
        # to scope it to.
        local legacy_broad_default="127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"

        # Captured before either key is read below, purely for
        # cmd_update's issue #579 gate -- presence (even of an explicit
        # empty value) means the operator already made a deliberate
        # MAGPIE_TRUSTED_PROXIES choice, so the gate must not re-fire.
        if [[ -n "${env_vars[MAGPIE_TRUSTED_PROXIES]+set}" || -n "${env_vars[TRUSTED_PROXIES]+set}" ]]; then
            TRUSTED_PROXIES_KEY_PRESENT="true"
        fi

        if [[ -n "${env_vars[MAGPIE_TRUSTED_PROXIES]+set}" ]]; then
            TRUSTED_PROXIES="${env_vars[MAGPIE_TRUSTED_PROXIES]}"
        elif [[ -n "${env_vars[TRUSTED_PROXIES]+set}" ]]; then
            TRUSTED_PROXIES="${env_vars[TRUSTED_PROXIES]}"
            if [[ "$TRUSTED_PROXIES" == "$legacy_broad_default" ]]; then
                log_warn "Migrating pre-#575 TRUSTED_PROXIES from ${INSTALL_DIR}/etc/.env: it is set to the old overly-broad default ($legacy_broad_default), which lets any client on those ranges spoof X-Forwarded-For and bypass MAGPIE_ALLOWED_CIDRS."
                log_warn "Edit MAGPIE_TRUSTED_PROXIES in ${INSTALL_DIR}/etc/.env to the exact upstream reverse proxy address(es) in front of this install (or leave it empty to trust none), then run '$SCRIPT_NAME update' to regenerate the Caddyfile."
            fi
        fi

        # MAGPIE_ALLOWED_CIDRS is never written by this script (only read
        # here, for cmd_update's issue #579 warning below) -- it reaches
        # docker-compose/Caddy straight from .env, which the operator edits
        # directly.
        ALLOWED_CIDRS="${env_vars[MAGPIE_ALLOWED_CIDRS]:-}"
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

    # TLS mode
    if [[ -z "$TLS_MODE" ]]; then
        prompt_choice "TLS mode" "off auto manual" "$DEFAULT_TLS_MODE" TLS_MODE
    fi

    # Domain (if TLS enabled)
    if [[ "$TLS_MODE" != "off" ]] && [[ -z "$DOMAIN" ]]; then
        prompt_value "Domain name (e.g., magpie.example.com)" "" DOMAIN
    fi

    # Certificate paths (if manual TLS)
    if [[ "$TLS_MODE" == "manual" ]]; then
        if [[ -z "$TLS_CERT" ]]; then
            prompt_value "TLS certificate path" "" TLS_CERT
        fi
        if [[ -z "$TLS_KEY" ]]; then
            prompt_value "TLS private key path" "" TLS_KEY
        fi
    fi

    # Ports
    if [[ -z "$HTTP_PORT" ]]; then
        HTTP_PORT="$DEFAULT_HTTP_PORT"
    fi
    if [[ -z "$HTTPS_PORT" ]]; then
        HTTPS_PORT="$DEFAULT_HTTPS_PORT"
    fi

    # Trusted proxies (X-Forwarded-For). Only relevant when Caddy sits
    # behind another reverse proxy -- typically a --tls-mode off (HTTP-only)
    # deployment fronted by an external proxy/load balancer. Prompt only in
    # that case; --tls-mode auto/manual means Caddy faces the internet
    # directly, so the empty (trust nothing) default is always correct
    # there. See issue #575.
    if [[ -z "$TRUSTED_PROXIES" ]]; then
        if [[ "$TLS_MODE" == "off" ]]; then
            prompt_value "Trusted proxy IP(s) (space-separated; the exact upstream reverse proxy in front of magpie, if any -- leave blank to trust none)" "$DEFAULT_TRUSTED_PROXIES" TRUSTED_PROXIES
        else
            TRUSTED_PROXIES="$DEFAULT_TRUSTED_PROXIES"
        fi
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
MAGPIE_HTTPS_PORT=${HTTPS_PORT}
MAGPIE_DOMAIN=${DOMAIN:-}

# Server settings
MAGPIE_DEBUG=false
MAGPIE_LOG_FORMAT=json
MAGPIE_RETENTION_DAYS=90

# TLS configuration (persisted for Caddyfile regeneration during updates)
# See issues #340 and #344. MAGPIE_DOMAIN above is the single canonical
# domain key -- load_existing_config() reads only that one, so there is no
# separate DOMAIN= key here to avoid a stale/hand-edited duplicate being
# silently ignored (issue #448).
TLS_MODE=${TLS_MODE:-}

# Trusted proxy IPs/CIDRs whose X-Forwarded-For header Caddy honors.
# Persisted with the MAGPIE_ prefix (unlike TLS_MODE/BIND_IP/ACME_SERVER
# above) because docker-compose passes it straight through to the caddy
# container as MAGPIE_TRUSTED_PROXIES, which Caddy itself reads via the
# trusted_proxies directive in the Caddyfile -- see issue #575.
MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES:-}

# Network configuration (issue #446)
BIND_IP=${BIND_IP:-}
ACME_SERVER=${ACME_SERVER:-}
EOF
}

generate_caddyfile() {
    # Generate Caddyfile by copying and patching Caddyfile.prod from repo
    # This ensures route definitions stay in sync with the canonical source
    # See issue #156 for planned templating improvements

    local source_caddyfile="${INSTALL_DIR}/repo/Caddyfile.prod"
    local dest_caddyfile="${INSTALL_DIR}/etc/Caddyfile"

    if [[ ! -f "$source_caddyfile" ]]; then
        die "Caddyfile.prod not found in cloned repo: $source_caddyfile"
    fi

    log "Generating Caddyfile (TLS mode: ${TLS_MODE})..."

    case "$TLS_MODE" in
        off)
            # HTTP-only mode for running behind a reverse proxy
            # - Replace site address with http://:80
            # - Add trusted_proxies for client IP preservation
            # - Remove HSTS header (not applicable to HTTP)

            # Start with header comment
            cat > "$dest_caddyfile" << EOF
# Magpie Caddyfile - TLS Mode: off (HTTP only, behind reverse proxy)
# Generated by magpie-deploy.sh v${MAGPIE_VERSION} from Caddyfile.prod
#
# Caddy listens on port 80 inside the container.
# Docker maps host:${HTTP_PORT} -> container:80

EOF
            # Copy the global options block, adding trusted_proxies.
            #
            # trusted_proxies reads {$MAGPIE_TRUSTED_PROXIES:} -- a literal
            # Caddy env-var placeholder, NOT bash-interpolated here (note
            # the escaped \$ below) -- so Caddy resolves it from its own
            # container's environment at config-load time, same as
            # Caddyfile.prod. That environment variable comes from .env via
            # docker-compose's `MAGPIE_TRUSTED_PROXIES=${MAGPIE_TRUSTED_PROXIES:-}`
            # (see docker-compose.yml), not from this script directly.
            # Default is empty: trust no proxy. See issue #575.
            cat >> "$dest_caddyfile" << EOF
{
	log {
		output stdout
		format json
		level INFO
	}
	admin off

	servers {
		trusted_proxies static {\$MAGPIE_TRUSTED_PROXIES:}
	}
}

EOF
            # Extract the site block contents (everything between the site address and final closing brace)
            # and wrap it with http://:80
            echo "http://:80 {" >> "$dest_caddyfile"

            # Extract route definitions from Caddyfile.prod (skip global block and site address)
            # Start after the site block opening, end before final closing brace
            # Use awk to properly track brace nesting depth (issue #273)
            #
            # Depth tracking explanation:
            # - The site block line "{$MAGPIE_DOMAIN} {" is SKIPPED with 'next', so we never count
            #   its opening brace. This is intentional: we want the CONTENT inside the block.
            # - We start at depth=0 (inside the site block, but before any nested blocks)
            # - Each nested block (header {}, handle {}, etc.) increments/decrements depth
            # - The internal content is balanced (each { has a matching }), so depth returns to 0
            # - The final closing brace of the site block (line 398) has no matching opener
            #   (since we skipped line 136), so it decrements depth to -1
            # - depth==-1 signals we've found the site block's closing brace
            #
            # Known limitation: Braces inside quoted strings (e.g., JSON responses) would be
            # counted. The current Caddyfile.prod has no such cases. If this becomes an issue,
            # a more sophisticated parser would be needed.
            awk '
                /^{\$MAGPIE_DOMAIN}/ {
                    in_site_block = 1
                    depth = 0
                    next
                }
                in_site_block {
                    # Count opening and closing braces
                    for (i = 1; i <= length($0); i++) {
                        c = substr($0, i, 1)
                        if (c == "{") depth++
                        if (c == "}") depth--
                    }

                    # If depth returns to -1, we found the final closing brace
                    if (depth == -1) {
                        exit
                    }

                    # Skip HSTS header (not applicable to HTTP-only mode)
                    if ($0 !~ /Strict-Transport-Security/) {
                        print
                    }
                }
            ' "$source_caddyfile" >> "$dest_caddyfile"

            echo "}" >> "$dest_caddyfile"
            ;;

        auto)
            # Automatic TLS (Let's Encrypt or custom ACME server)
            # - Replace {$MAGPIE_DOMAIN} with the actual domain
            # - Add a custom ACME server if configured
            #
            # DOMAIN and ACME_SERVER are substituted with bash's own literal
            # string replacement and matched with awk -v (not sed), and are
            # validated (validate_config/validate_network_config) before
            # this ever runs, so neither value is interpreted as a regex or
            # sed script. See issue #448.
            local content
            content="$(cat "$source_caddyfile")"
            content="${content//\{\$MAGPIE_DOMAIN\}/$DOMAIN}"
            printf '%s\n' "$content" > "$dest_caddyfile"

            if [[ -n "$ACME_SERVER" ]]; then
                # Insert a tls block with the custom ACME server directly
                # after the site address line.
                #
                # ACME_SERVER is passed via ENVIRON, not `awk -v`: `-v`
                # decodes backslash escapes (\n, octal \173/\175/\040, ...)
                # in the assigned value before the awk program ever runs,
                # which would let an escape-encoded payload with zero
                # literal blocked bytes decode into real braces/newlines
                # here -- even though it passed the ACME_SERVER allowlist.
                # ENVIRON does not decode escapes. This is deliberately
                # belt-and-suspenders with the strict allowlist in
                # is_valid_acme_server_url(), which rejects backslashes
                # outright. See issue #448.
                ACME_SERVER="$ACME_SERVER" awk -v site="${DOMAIN} {" '
                    BEGIN { acme = ENVIRON["ACME_SERVER"] }
                    {
                        print
                        if ($0 == site) {
                            print "\ttls {"
                            print "\t\tca " acme
                            print "\t}"
                        }
                    }
                ' "$dest_caddyfile" > "${dest_caddyfile}.tmp"
                mv "${dest_caddyfile}.tmp" "$dest_caddyfile"
            fi

            # Add generation header
            {
                echo "# Generated by magpie-deploy.sh v${MAGPIE_VERSION} (TLS mode: auto)"
                cat "$dest_caddyfile"
            } > "${dest_caddyfile}.tmp"
            mv "${dest_caddyfile}.tmp" "$dest_caddyfile"
            ;;

        manual)
            # Manual TLS with user-provided certificates
            # - Copy certs to install dir so they're managed with the installation
            # - Replace {$MAGPIE_DOMAIN} with the actual domain
            # - Add tls directive with container paths
            #
            # DOMAIN is substituted with bash's own literal string
            # replacement and matched with awk -v (not sed), and is
            # validated (validate_config/validate_network_config) before
            # this ever runs. See issue #448.

            # Copy certificates to installation directory
            local tls_dir="${INSTALL_DIR}/etc/tls"
            mkdir -p "$tls_dir"
            log "Copying TLS certificates to ${tls_dir}..."
            cp "$TLS_CERT" "${tls_dir}/cert.pem"
            cp "$TLS_KEY" "${tls_dir}/key.pem"
            chmod 644 "${tls_dir}/cert.pem"
            chmod 600 "${tls_dir}/key.pem"

            local content
            content="$(cat "$source_caddyfile")"
            content="${content//\{\$MAGPIE_DOMAIN\}/$DOMAIN}"
            printf '%s\n' "$content" > "$dest_caddyfile"

            # Add tls directive with container paths (certs mounted at /etc/caddy/tls/)
            awk -v site="${DOMAIN} {" '
                {
                    print
                    if ($0 == site) {
                        print "\ttls /etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem"
                    }
                }
            ' "$dest_caddyfile" > "${dest_caddyfile}.tmp"
            mv "${dest_caddyfile}.tmp" "$dest_caddyfile"

            # Add generation header
            {
                echo "# Generated by magpie-deploy.sh v${MAGPIE_VERSION} (TLS mode: manual)"
                cat "$dest_caddyfile"
            } > "${dest_caddyfile}.tmp"
            mv "${dest_caddyfile}.tmp" "$dest_caddyfile"
            ;;
    esac

    log "Caddyfile generated at ${dest_caddyfile}"
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

ExecStart=/usr/bin/docker compose --env-file ${INSTALL_DIR}/etc/.env up --no-build
ExecStop=/usr/bin/docker compose --env-file ${INSTALL_DIR}/etc/.env down

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
ExecStart=/usr/bin/flock -n /var/run/magpie-gc.lock /usr/bin/docker compose --env-file ${INSTALL_DIR}/etc/.env run --rm -T magpie magpie-ctl gc --quiet

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

    local image_tag="${GHCR_IMAGE}:${remote_version}"
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

    # Copy compose files to install directory
    cp "${repo_dir}/docker-compose.yml" "${INSTALL_DIR}/"
    cp "${repo_dir}/docker-compose.prod.yml" "${INSTALL_DIR}/" 2>/dev/null || true

    log "Repository cloned successfully"
}

patch_compose_for_caddyfile() {
    # Patch docker-compose.yml to mount our generated Caddyfile
    log "Patching Docker Compose for custom Caddyfile..."

    local compose_file="${INSTALL_DIR}/docker-compose.yml"
    local pattern="./Caddyfile:/etc/caddy/Caddyfile:ro"

    # Validate that the pattern exists before patching
    # Use -F for fixed string matching (. is literal, not regex wildcard)
    if ! grep -qF "$pattern" "$compose_file"; then
        die "Cannot patch docker-compose.yml: expected Caddyfile mount pattern not found.\nExpected: $pattern"
    fi

    # Replace the Caddyfile mount path
    sed -i "s|./Caddyfile:/etc/caddy/Caddyfile:ro|${INSTALL_DIR}/etc/Caddyfile:/etc/caddy/Caddyfile:ro|g" \
        "$compose_file"

    # Validate that the replacement succeeded
    if grep -qF "$pattern" "$compose_file"; then
        die "Failed to patch docker-compose.yml: Caddyfile mount pattern was not replaced"
    fi

    if ! grep -qF "${INSTALL_DIR}/etc/Caddyfile:/etc/caddy/Caddyfile:ro" "$compose_file"; then
        die "Failed to patch docker-compose.yml: new Caddyfile mount path not found after replacement"
    fi
}

patch_compose_for_tls_certs() {
    # Add TLS certificate mount for manual TLS mode
    # Only called when TLS_MODE=manual

    if [[ "$TLS_MODE" != "manual" ]]; then
        return 0
    fi

    log "Patching Docker Compose for TLS certificates..."

    local compose_file="${INSTALL_DIR}/docker-compose.yml"
    local caddyfile_mount="${INSTALL_DIR}/etc/Caddyfile:/etc/caddy/Caddyfile:ro"
    local tls_mount="${INSTALL_DIR}/etc/tls:/etc/caddy/tls:ro"

    # Add TLS mount after the Caddyfile mount in the caddy service
    if grep -q "$tls_mount" "$compose_file"; then
        log "TLS mount already present in docker-compose.yml"
        return 0
    fi

    # Insert TLS mount line after the Caddyfile mount
    sed -i "s|${caddyfile_mount}|${caddyfile_mount}\n      - ${tls_mount}|g" "$compose_file"

    # Verify the mount was added
    if ! grep -q "$tls_mount" "$compose_file"; then
        die "Failed to add TLS certificate mount to docker-compose.yml"
    fi

    log "TLS certificate mount added to docker-compose.yml"
}

patch_compose_for_https_port() {
    # Remove HTTPS port mapping when TLS is off
    #
    # When TLS mode is off, Caddy does not listen on port 443 — the HTTPS port
    # mapping serves no purpose and causes bind failures if the port is already
    # in use on the host (e.g. port 8443 occupied by Authentik).
    #
    # Docker Compose has no native way to conditionally include a port mapping
    # based on an env var value (the :-default syntax always maps the port), so
    # we patch the file directly.
    #
    # See issue #506.

    if [[ "$TLS_MODE" != "off" ]]; then
        return 0
    fi

    log "TLS mode is off — removing HTTPS port mapping from Docker Compose..."

    local compose_file="${INSTALL_DIR}/docker-compose.yml"

    # Remove the HTTPS port line entirely.
    # This runs before patch_compose_for_bind_ip, so the line still has its
    # original form: - "${MAGPIE_HTTPS_PORT:-8443}:443"
    sed -i '/"\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"/d' "$compose_file"

    # Verify the line is gone
    if grep -q 'MAGPIE_HTTPS_PORT' "$compose_file"; then
        die "Failed to remove HTTPS port mapping from docker-compose.yml"
    fi

    log "HTTPS port mapping removed (TLS off — port 443 will not be mapped to host)"
}

patch_compose_for_bind_ip() {
    # Patch port bindings to use specific IP address
    # Only called when BIND_IP is set

    if [[ -z "$BIND_IP" ]]; then
        return 0
    fi

    log "Patching Docker Compose for IP binding: ${BIND_IP}..."

    local compose_file="${INSTALL_DIR}/docker-compose.yml"

    # Replace port bindings to include IP prefix
    # Original: - "${MAGPIE_HTTP_PORT:-8080}:80"
    # New:      - "${BIND_IP}:${MAGPIE_HTTP_PORT:-8080}:80"
    sed -i 's|- "\${MAGPIE_HTTP_PORT:-[0-9]*}:80"|- "${BIND_IP}:${MAGPIE_HTTP_PORT:-8080}:80"|g' "$compose_file"
    sed -i 's|- "\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"|- "${BIND_IP}:${MAGPIE_HTTPS_PORT:-8443}:443"|g' "$compose_file"

    # Verify the changes were applied
    if ! grep -q '${BIND_IP}:${MAGPIE_HTTP_PORT' "$compose_file"; then
        die "Failed to patch docker-compose.yml for bind IP"
    fi

    log "Docker Compose patched for IP binding"
}

pull_or_build_image() {
    if [[ "$FROM_SOURCE" == "true" ]]; then
        log "Building magpie Docker image from source (this may take a few minutes)..."
        cd "${INSTALL_DIR}/repo"
        if ! docker build --pull -t magpie:latest .; then
            die "Failed to build magpie image"
        fi
        log "Image built successfully"
    else
        local image_tag="${GHCR_IMAGE}:${MAGPIE_VERSION}"
        log "Pulling magpie image from container registry..."
        log "  Image: ${image_tag}"
        if ! docker pull "$image_tag"; then
            die "Failed to pull magpie image from ${image_tag}\nThe image tag is derived from the version in the cloned repo's pyproject.toml (${MAGPIE_VERSION}). If you used --release, confirm a release was published for that version."
        fi
        # Tag as magpie:latest for compose compatibility
        docker tag "$image_tag" magpie:latest
        log "Image pulled successfully"
    fi
}

patch_compose_for_local_image() {
    # Replace 'build: .' with 'image: magpie:latest' so we use our pre-built/pulled image
    log "Configuring Docker Compose to use local image..."

    sed -i 's|build: \.|image: magpie:latest|g' "${INSTALL_DIR}/docker-compose.yml"
}

# =============================================================================
# Service management
# =============================================================================

pull_images() {
    log "Pulling Docker images..."
    cd "$INSTALL_DIR"
    docker compose --env-file "${INSTALL_DIR}/etc/.env" pull
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

    cd "$INSTALL_DIR"

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
        if output=$(docker compose --env-file "${INSTALL_DIR}/etc/.env" exec -T -e MAGPIE_ADMIN_TOKEN_SINK=stdout magpie magpie-ctl init 2>&1); then
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
        # container's own first-boot init (entrypoint.sh, running before
        # this script's health check returns) already generated and
        # delivered it via MAGPIE_ADMIN_TOKEN_SINK (default: file, at
        # ${DATA_DIR}/admin-token) before this exec ever ran.
        log "Database already initialized; admin token was delivered on first boot."
        log "Default sink is 'file': sudo cat ${DATA_DIR}/admin-token"
        log "To mint an additional admin-scope token instead:"
        log "  docker compose --env-file ${INSTALL_DIR}/etc/.env exec magpie magpie-ctl token create --name ops-admin --scope admin"
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

    # Create directory structure
    log "Creating directory structure..."
    mkdir -p "${INSTALL_DIR}/etc"
    mkdir -p "${DATA_DIR}/artifacts"

    # Clone repo first (needed for Caddyfile.prod and Dockerfile)
    clone_repo
    detect_version

    # Generate configuration files
    generate_env_file
    generate_caddyfile  # Uses Caddyfile.prod from cloned repo
    generate_systemd_service
    generate_gc_units

    # Pull or build image and configure compose
    pull_or_build_image
    patch_compose_for_caddyfile
    patch_compose_for_tls_certs
    patch_compose_for_https_port
    patch_compose_for_bind_ip
    patch_compose_for_local_image

    # Pull Caddy image
    log "Pulling Caddy image..."
    docker pull caddy:2-alpine

    # Start everything
    start_services
    wait_for_healthy
    run_init

    echo ""
    log "Installation complete!"
    echo ""
    echo "  Installation directory: ${INSTALL_DIR}"
    echo "  Data directory: ${DATA_DIR}"
    echo "  TLS mode: ${TLS_MODE}"
    if [[ "$TLS_MODE" == "off" ]]; then
        echo "  Listening on: http://127.0.0.1:${HTTP_PORT}"
        echo ""
        echo "  Configure your reverse proxy to forward to this address."
    else
        echo "  Domain: ${DOMAIN}"
    fi
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
# returns 0), or decline -- which aborts the update via die(). Deliberately
# not built on confirm()/prompt_value(): those honor --yes/NONINTERACTIVE
# defaults, which would let an unrelated --yes (e.g. for uninstall
# confirmations) silently answer this security-relevant question. Only
# called when NONINTERACTIVE is already known false.
prompt_trusted_proxies_for_cidr_allow() {
    local response
    while true; do
        read -r -p "[magpie] Upstream proxy hop as seen by magpie's Caddy (e.g. 172.20.0.0/16), or leave blank if magpie is directly exposed: " response
        # Whitespace-only counts as blank too -- is_valid_ip_or_cidr_list
        # tokenizes an all-whitespace string to zero tokens and would
        # otherwise accept it as a trivially "valid" empty list, silently
        # persisting an empty MAGPIE_TRUSTED_PROXIES for what may have been
        # a fat-fingered proxy hop. Blank must always land on the explicit
        # direct-exposure confirmation below, never be silently accepted
        # here.
        if [[ -z "$response" || "$response" =~ ^[[:space:]]+$ ]]; then
            break
        fi
        if is_valid_ip_or_cidr_list "$response"; then
            TRUSTED_PROXIES="$response"
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
            die "Update aborted -- MAGPIE_TRUSTED_PROXIES was not confirmed. Re-run '$SCRIPT_NAME update' and either enter the proxy hop or confirm direct exposure."
            ;;
    esac
}

# Tiered response for cmd_update when MAGPIE_ALLOWED_CIDRS is a real range
# and MAGPIE_TRUSTED_PROXIES resolves empty, keyed on TLS_MODE as the "am I
# fronted?" signal (tls-mode off => almost certainly behind an external
# terminator; auto/manual => magpie is the edge, so empty is likely
# correct). See issue #579's scope-expansion comment for the full rationale.
#
# tls-mode off (HIGH-RISK): if MAGPIE_TRUSTED_PROXIES has never been
# configured for this install (TRUSTED_PROXIES_KEY_PRESENT is false -- the
# legacy-key case is already handled by load_existing_config()'s
# migrate+warn path above and always leaves TRUSTED_PROXIES non-empty, so
# it can't reach here) and the operator didn't pass --trusted-proxies or
# --accept-empty-trusted-proxies this run, GATE: prompt interactively, or
# hard-fail via die() when NONINTERACTIVE. Fires once -- cmd_update's
# existing MAGPIE_TRUSTED_PROXIES writeback (below, near the legacy-key
# cleanup) persists whatever TRUSTED_PROXIES resolves to once the gate is
# satisfied, including an explicit empty value, so TRUSTED_PROXIES_KEY_PRESENT
# is true on every subsequent run.
#
# tls-mode auto/manual (LOWER-RISK): advisory only, never blocks.
warn_or_gate_trusted_proxies_for_cidr_allow() {
    # 255.255.255.255/32 is the Caddyfile's own placeholder default for an
    # unset MAGPIE_ALLOWED_CIDRS (see Caddyfile.prod's `client_ip
    # {$MAGPIE_ALLOWED_CIDRS:255.255.255.255/32}`) -- never a real client, so
    # treat it the same as empty.
    local off_sentinel="255.255.255.255/32"

    [[ -z "$ALLOWED_CIDRS" || "$ALLOWED_CIDRS" == "$off_sentinel" ]] && return 0
    [[ -n "$TRUSTED_PROXIES" ]] && return 0

    if [[ "$TLS_MODE" != "off" ]]; then
        # Same "key present = deliberate choice" signal the gate below
        # uses -- once an explicit empty MAGPIE_TRUSTED_PROXIES= is
        # persisted (e.g. by a prior --tls-mode off run, or hand-edited),
        # don't nag a settled install on every subsequent update.
        if [[ "$TRUSTED_PROXIES_KEY_PRESENT" != "true" ]]; then
            log_warn "MAGPIE_ALLOWED_CIDRS is set but MAGPIE_TRUSTED_PROXIES is empty."
            log_warn "As of v0.1.6 the built-in Caddy trusts no proxy by default. If magpie is behind a reverse proxy, CIDR-based anonymous reads will NO LONGER match real clients (they will 401) until you set MAGPIE_TRUSTED_PROXIES in ${INSTALL_DIR}/etc/.env to your proxy's hop as seen by magpie's Caddy -- commonly magpie's docker bridge subnet, e.g. 172.20.0.0/16 (or the gateway /32) -- and re-run '$SCRIPT_NAME update'. If magpie is directly exposed (no proxy), no action is needed."
        fi
        return 0
    fi

    # tls-mode off from here on -- the high-risk gate.
    if [[ "$TRUSTED_PROXIES_KEY_PRESENT" == "true" ]]; then
        # Already a deliberate, persisted choice (even if empty) -- silent.
        return 0
    fi
    if [[ "$ACCEPT_EMPTY_TRUSTED_PROXIES" == "true" ]]; then
        log "Proceeding with empty MAGPIE_TRUSTED_PROXIES (--accept-empty-trusted-proxies)."
        return 0
    fi

    local detected="tls-mode is 'off' and MAGPIE_ALLOWED_CIDRS=${ALLOWED_CIDRS} is set for anonymous CIDR-based reads, but MAGPIE_TRUSTED_PROXIES has never been configured for this install. As of v0.1.6 Caddy trusts no proxy by default, so if magpie sits behind a reverse proxy, the real client IP is lost and every anonymous CIDR read will 401 after this update."
    local fix="Fix: set MAGPIE_TRUSTED_PROXIES to the upstream reverse proxy's hop as seen by magpie's Caddy -- commonly the docker bridge subnet, e.g. 172.20.0.0/16 (or the gateway /32) -- via --trusted-proxies <value> or by editing ${INSTALL_DIR}/etc/.env, then re-run '$SCRIPT_NAME update'."
    local bypass="Bypass: if magpie is directly exposed (no reverse proxy), pass --accept-empty-trusted-proxies to proceed with an empty MAGPIE_TRUSTED_PROXIES."

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        log_error "$detected"
        log_error "$fix"
        die "$bypass"
    fi

    log_warn "$detected"
    log_warn "$fix"
    log_warn "$bypass"
    prompt_trusted_proxies_for_cidr_allow
}

cmd_update() {
    log "Updating magpie..."

    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

    # Unlike DATA_DIR, INSTALL_DIR is not persisted to .env -- it comes
    # straight from --install-dir on this invocation (or the default). It's
    # later interpolated into `sed 's|...|${INSTALL_DIR}/...|g'` in
    # patch_compose_for_caddyfile()/patch_compose_for_tls_certs(), so an
    # unvalidated value containing '|' or other sed metacharacters could
    # corrupt or hijack docker-compose.yml. cmd_install validates this via
    # validate_config(); cmd_update needs its own check. See issue #448.
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

    # Resolved here (rather than only in the Caddyfile-regeneration block
    # below) so the issue #579 tls-mode check below sees the real value for
    # pre-#345 .env files that predate the TLS_MODE key. Idempotent -- the
    # Caddyfile block re-applies the same default further down.
    TLS_MODE="${TLS_MODE:-$DEFAULT_TLS_MODE}"
    warn_or_gate_trusted_proxies_for_cidr_allow

    cd "$INSTALL_DIR"

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

    # Update compose files from repo
    log "Updating docker-compose files..."
    cp "${INSTALL_DIR}/repo/docker-compose.yml" "${INSTALL_DIR}/"
    cp "${INSTALL_DIR}/repo/docker-compose.prod.yml" "${INSTALL_DIR}/" 2>/dev/null || true

    # Update Caddyfile from repo
    log "Updating Caddyfile using Caddyfile.prod and existing configuration..."
    if [[ ! -f "${INSTALL_DIR}/repo/Caddyfile.prod" ]]; then
        log_warn "Caddyfile.prod not found in repo, skipping Caddyfile update"
    else
        # TLS_MODE, DOMAIN, TRUSTED_PROXIES, BIND_IP, and ACME_SERVER were
        # already loaded from .env by load_existing_config() above, using a
        # parser that performs no shell expansion or execution of the
        # file's contents -- no need to source the file again here.
        # See issues #340, #344, and #448 for background.

        # Apply defaults for vars that may be missing from old .env files (migration from pre-#345 installs)
        TLS_MODE="${TLS_MODE:-$DEFAULT_TLS_MODE}"
        TRUSTED_PROXIES="${TRUSTED_PROXIES:-$DEFAULT_TRUSTED_PROXIES}"

        # Re-validate the values loaded from .env before they're used to
        # regenerate the Caddyfile -- guards against a hand-edited or
        # pre-#448 .env file containing a value that would no longer pass
        # validation. Does not call the full validate_config(), since
        # TLS_CERT/TLS_KEY (manual-mode certificate paths) are per-install
        # CLI args, not persisted to .env, and would spuriously fail here.
        local errors=()
        validate_network_config
        if [[ ${#errors[@]} -gt 0 ]]; then
            log_error "Existing configuration in .env failed validation; refusing to regenerate Caddyfile:"
            for err in "${errors[@]}"; do
                echo "  - $err" >&2
            done
            die "Fix or remove the invalid value(s) in ${INSTALL_DIR}/etc/.env, or reinstall."
        fi

        # `update` never regenerates .env (only `install` calls
        # generate_env_file()), so a pre-#575 install's .env may still only
        # have the legacy unprefixed TRUSTED_PROXIES key that
        # load_existing_config() just migrated into this script's
        # TRUSTED_PROXIES variable above. That in-memory value alone is not
        # enough: docker-compose reads .env directly (not through this
        # script) and substitutes MAGPIE_TRUSTED_PROXIES specifically into
        # the caddy container's environment. Write the canonical key back
        # to .env now so the regenerated Caddyfile and the running
        # container agree. See issue #575.
        if ! grep -q '^MAGPIE_TRUSTED_PROXIES=' "${INSTALL_DIR}/etc/.env" 2>/dev/null; then
            log "Persisting MAGPIE_TRUSTED_PROXIES=${TRUSTED_PROXIES} to ${INSTALL_DIR}/etc/.env (migrated from legacy TRUSTED_PROXIES key)"
            printf 'MAGPIE_TRUSTED_PROXIES=%s\n' "$TRUSTED_PROXIES" >> "${INSTALL_DIR}/etc/.env"
        fi

        # Drop the stale legacy unprefixed TRUSTED_PROXIES= key now that its
        # value is guaranteed to be carried forward in MAGPIE_TRUSTED_PROXIES
        # (either already present above, or just written by the block above)
        # -- it's never read by docker-compose, so leaving it in place is
        # harmless but confusing clutter for an operator hand-inspecting
        # .env. Gated on MAGPIE_TRUSTED_PROXIES actually being present so the
        # value is never dropped without first being carried forward.
        # Anchored on the key at line start (^TRUSTED_PROXIES=, which does
        # not match ^MAGPIE_TRUSTED_PROXIES=) so this never touches an
        # unrelated line that merely contains the substring.
        if grep -q '^MAGPIE_TRUSTED_PROXIES=' "${INSTALL_DIR}/etc/.env" 2>/dev/null \
            && grep -q '^TRUSTED_PROXIES=' "${INSTALL_DIR}/etc/.env" 2>/dev/null; then
            log "Removing stale legacy TRUSTED_PROXIES key from ${INSTALL_DIR}/etc/.env (superseded by MAGPIE_TRUSTED_PROXIES)"
            sed -i '/^TRUSTED_PROXIES=/d' "${INSTALL_DIR}/etc/.env"
        fi

        # Regenerate the Caddyfile using the configured (or defaulted) TLS settings
        if declare -F generate_caddyfile >/dev/null 2>&1; then
            generate_caddyfile
        else
            log_warn "generate_caddyfile() not found; falling back to direct Caddyfile copy"
            cp "${INSTALL_DIR}/repo/Caddyfile.prod" "${INSTALL_DIR}/etc/Caddyfile"
        fi
    fi

    # Re-patch compose files for deployment
    patch_compose_for_caddyfile
    patch_compose_for_tls_certs
    patch_compose_for_https_port
    patch_compose_for_bind_ip
    patch_compose_for_local_image

    if [[ "$FROM_SOURCE" == "true" ]]; then
        log "Rebuilding magpie image from source..."
        if ! docker build --pull -t magpie:latest "${INSTALL_DIR}/repo"; then
            die "Failed to rebuild magpie image"
        fi
    else
        local image_tag="${GHCR_IMAGE}:${MAGPIE_VERSION}"
        log "Pulling magpie image from container registry..."
        log "  Image: ${image_tag}"
        if ! docker pull "$image_tag"; then
            die "Failed to pull magpie image from ${image_tag}"
        fi
        docker tag "$image_tag" magpie:latest
    fi

    log "Pulling external images..."
    # Only pull caddy - magpie image is already handled above
    docker compose --env-file "${INSTALL_DIR}/etc/.env" pull caddy

    log "Restarting services..."
    systemctl restart magpie.service

    wait_for_healthy

    log "Update complete!"
    echo ""
    echo "  Updated to version: ${MAGPIE_VERSION}"
    echo "  Note: docker-compose.yml and Caddyfile have been regenerated from the repository."
    echo "  Any local customizations to these files have been overwritten; review and reapply as needed."
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

    cd "$INSTALL_DIR"
    log "Removing containers and volumes..."
    docker compose --env-file "${INSTALL_DIR}/etc/.env" down --volumes 2>/dev/null || true

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
    cd "$INSTALL_DIR"
    docker compose --env-file "${INSTALL_DIR}/etc/.env" ps 2>/dev/null || echo "  No containers"
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

    cd "$INSTALL_DIR"

    # Validate --lines before it reaches docker compose. See issue #448.
    if ! [[ "$LINES" =~ ^[0-9]+$ ]]; then
        die "Invalid --lines value: $LINES (must be a non-negative integer)"
    fi

    # Build the docker-compose argument list as a quoted array rather than
    # interpolating into an unquoted command line. See issue #448.
    local compose_args=(--env-file "${INSTALL_DIR}/etc/.env" logs)
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
  --tls-mode MODE         TLS mode: off, auto, manual (default: $DEFAULT_TLS_MODE)
  --domain DOMAIN         Domain name (required for auto/manual TLS)
  --tls-cert PATH         TLS certificate path (required for manual TLS)
  --tls-key PATH          TLS key path (required for manual TLS)
  --http-port PORT        HTTP port (default: $DEFAULT_HTTP_PORT)
  --https-port PORT       HTTPS port (default: $DEFAULT_HTTPS_PORT)
  --bind-ip IP            Bind to specific IP address (default: all interfaces)
                          Example: --bind-ip 10.3.3.107
  --acme-server URL       Custom ACME server URL (only with --tls-mode auto)
                          Example: --acme-server https://ca.example.com/acme/acme/directory
                          Default: Let's Encrypt
  --trusted-proxies CIDR  Space-separated IPs/CIDRs whose X-Forwarded-For
                          header Caddy trusts (default: none -- the real
                          connecting peer's IP is used). Only set this to
                          the exact upstream hop(s) when Caddy sits behind
                          another reverse proxy (e.g. --tls-mode off);
                          never a broad range. See issue #575. Also
                          accepted by 'update' to satisfy the issue #579
                          gate below on a fronted install that has never
                          configured MAGPIE_TRUSTED_PROXIES.
  --noninteractive        Skip interactive prompts (use defaults for all config)
  --force                 Overwrite existing installation
  --from-source           Build image from source instead of pulling from ghcr.io

Update options:
  --from-source                    Rebuild image from source instead of
                                    pulling from ghcr.io
  --trusted-proxies CIDR           See Install options above -- also
                                    applies to 'update'.
  --accept-empty-trusted-proxies   Acknowledge that an empty
                                    MAGPIE_TRUSTED_PROXIES is intentional
                                    (magpie is directly exposed, no reverse
                                    proxy). Only meaningful when this
                                    install's configured TLS mode
                                    (persisted in INSTALL_DIR/etc/.env) is
                                    'off' and 'update' finds a real
                                    MAGPIE_ALLOWED_CIDRS with no
                                    MAGPIE_TRUSTED_PROXIES ever configured
                                    for this install -- without it (or
                                    --trusted-proxies), that combination
                                    prompts interactively or, with
                                    --noninteractive, hard-fails the
                                    update. See issue #579.

Uninstall options:
  --yes, -y               Skip confirmation prompts (auto-confirm uninstall)
  --purge                 Also remove data directory (requires --yes for non-interactive)
                          Without --purge: preserves data directory for reinstallation
                          With --purge: permanently deletes all artifacts and data

Logs options:
  -f, --follow            Follow log output
  -n, --lines N           Number of lines to show (default: 100)

Examples:
  # Interactive installation
  sudo $SCRIPT_NAME install

  # Non-interactive installation (HTTP-only, behind proxy)
  sudo $SCRIPT_NAME install --tls-mode off --noninteractive

  # Installation with custom data directory
  sudo $SCRIPT_NAME install --data-dir /srv/magpie/data

  # Installation with Let's Encrypt
  sudo $SCRIPT_NAME install --tls-mode auto --domain magpie.example.com

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
            --tls-mode)
                TLS_MODE="$2"
                shift 2
                ;;
            --domain)
                DOMAIN="$2"
                shift 2
                ;;
            --tls-cert)
                TLS_CERT="$2"
                shift 2
                ;;
            --tls-key)
                TLS_KEY="$2"
                shift 2
                ;;
            --http-port)
                HTTP_PORT="$2"
                shift 2
                ;;
            --https-port)
                HTTPS_PORT="$2"
                shift 2
                ;;
            --trusted-proxies)
                TRUSTED_PROXIES="$2"
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
            --bind-ip)
                BIND_IP="$2"
                shift 2
                ;;
            --acme-server)
                ACME_SERVER="$2"
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

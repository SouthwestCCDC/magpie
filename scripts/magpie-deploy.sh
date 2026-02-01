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

# =============================================================================
# Constants
# =============================================================================

SCRIPT_NAME="$(basename "$0")"
GITHUB_REPO="SouthwestCCDC/magpie"
GITHUB_BRANCH="default"
GHCR_IMAGE="ghcr.io/southwestccdc/magpie"
MAGPIE_VERSION=""  # Dynamically detected from pyproject.toml after cloning repo
# For --version output before repo clone, display "dev" (cosmetic only).
HARDCODED_VERSION="dev"

# Default configuration
DEFAULT_INSTALL_DIR="/opt/magpie"
DEFAULT_TLS_MODE="off"
DEFAULT_HTTP_PORT="8080"
DEFAULT_HTTPS_PORT="8443"
DEFAULT_TRUSTED_PROXIES="127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"

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
NONINTERACTIVE="false"
FORCE="false"
PURGE="false"
YES="false"
FOLLOW="false"
LINES="100"
FROM_SOURCE="false"

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

    # Directory validation
    if [[ -z "$INSTALL_DIR" ]]; then
        errors+=("Install directory cannot be empty")
    elif [[ ! "$INSTALL_DIR" =~ ^/ ]]; then
        errors+=("Install directory must be an absolute path: $INSTALL_DIR")
    fi

    # Data directory validation
    if [[ -z "$DATA_DIR" ]]; then
        errors+=("Data directory cannot be empty")
    elif [[ ! "$DATA_DIR" =~ ^/ ]]; then
        errors+=("Data directory must be an absolute path: $DATA_DIR")
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

load_existing_config() {
    if [[ -f "${INSTALL_DIR}/etc/.env" ]]; then
        # shellcheck source=/dev/null
        source "${INSTALL_DIR}/etc/.env"

        # Map env vars to script variables (only for MAGPIE_* prefixed vars)
        DATA_DIR="${MAGPIE_DATA_DIR:-$DATA_DIR}"
        HTTP_PORT="${MAGPIE_HTTP_PORT:-$HTTP_PORT}"
        HTTPS_PORT="${MAGPIE_HTTPS_PORT:-$HTTPS_PORT}"
        DOMAIN="${MAGPIE_DOMAIN:-$DOMAIN}"
        # TLS_MODE and TRUSTED_PROXIES are loaded directly (no prefix mapping needed)
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

    # Trusted proxies
    if [[ -z "$TRUSTED_PROXIES" ]]; then
        TRUSTED_PROXIES="$DEFAULT_TRUSTED_PROXIES"
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
# See issues #340 and #344
TLS_MODE=${TLS_MODE:-}
DOMAIN=${DOMAIN:-}
TRUSTED_PROXIES=${TRUSTED_PROXIES:-}

# Caddy configuration (consolidated Caddyfile)
# Simple single-line environment variables are set here.
# Complex multiline variables (MAGPIE_ENABLE_LOGGING, MAGPIE_PROD_SECURITY_HEADERS)
# must be set manually in docker-compose.prod.yml or systemd service override.
# See issue #232 for the consolidated Caddyfile design.
MAGPIE_SITE_ADDRESS=${DOMAIN:-:80}
MAGPIE_DISABLE_ADMIN=admin off
MAGPIE_ALLOWED_CIDRS=${MAGPIE_ALLOWED_CIDRS:-255.255.255.255/32}
EOF

    # Add trusted_proxies to global options for HTTP-only mode
    if [[ "$TLS_MODE" == "off" && -n "$TRUSTED_PROXIES" ]]; then
        # Note: The consolidated Caddyfile uses 'trusted_proxies static private_ranges'
        # by default. For the deployment script to support custom TRUSTED_PROXIES,
        # we would need to add MAGPIE_TRUSTED_PROXIES to the Caddyfile.
        # For now, document this limitation.
        log_warn "Note: TRUSTED_PROXIES setting requires manual Caddyfile customization in v0.2.0+"
        log_warn "      The consolidated Caddyfile uses 'trusted_proxies static private_ranges'"
    fi
}

generate_caddyfile() {
    # Copy consolidated Caddyfile from repo
    # Configuration is controlled via environment variables set in generate_env_file()
    # See issue #232 for the consolidated Caddyfile design

    local source_caddyfile="${INSTALL_DIR}/repo/Caddyfile"
    local dest_caddyfile="${INSTALL_DIR}/etc/Caddyfile"

    if [[ ! -f "$source_caddyfile" ]]; then
        die "Caddyfile not found in cloned repo: $source_caddyfile"
    fi

    log "Copying consolidated Caddyfile (TLS mode: ${TLS_MODE})..."

    # Copy the consolidated Caddyfile
    cp "$source_caddyfile" "$dest_caddyfile"

    # For manual TLS mode, copy certificates to installation directory
    if [[ "$TLS_MODE" == "manual" ]]; then
        local tls_dir="${INSTALL_DIR}/etc/tls"
        mkdir -p "$tls_dir"
        log "Copying TLS certificates to ${tls_dir}..."
        cp "$TLS_CERT" "${tls_dir}/cert.pem"
        cp "$TLS_KEY" "${tls_dir}/key.pem"
        chmod 644 "${tls_dir}/cert.pem"
        chmod 600 "${tls_dir}/key.pem"
    fi

    log "Caddyfile copied to ${dest_caddyfile}"
    log "Basic Caddy configuration via MAGPIE_SITE_ADDRESS and MAGPIE_DISABLE_ADMIN in ${INSTALL_DIR}/etc/.env"
    log "For multiline production config (logging, security headers), edit docker-compose.prod.yml or systemd override"
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

detect_version() {
    # Extract version from pyproject.toml
    # This function should be called after clone_repo() to ensure the repo exists
    local pyproject="${INSTALL_DIR}/repo/pyproject.toml"

    if [[ ! -f "$pyproject" ]]; then
        die "Cannot detect version: pyproject.toml not found at $pyproject"
    fi

    # Extract version using robust sed with extended regex
    # Format: version = "0.1.0-rc8"
    # Handles flexible whitespace around = and ensures only first match
    MAGPIE_VERSION=$(sed -nE 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' "$pyproject" | head -n1)

    if [[ -z "$MAGPIE_VERSION" ]]; then
        die "Failed to extract version from $pyproject"
    fi

    log "Detected magpie version: ${MAGPIE_VERSION}"
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
            die "Failed to pull magpie image from ${image_tag}"
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

        if output=$(docker compose --env-file "${INSTALL_DIR}/etc/.env" exec -T magpie magpie-ctl init 2>&1); then
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
        log "Database initialized (admin token already exists)"
    fi
}

# =============================================================================
# Commands
# =============================================================================

cmd_install() {
    log "Starting magpie installation..."

    check_prerequisites
    gather_config
    validate_config
    check_existing_installation

    # Create directory structure
    log "Creating directory structure..."
    mkdir -p "${INSTALL_DIR}/etc"
    mkdir -p "${DATA_DIR}/artifacts"

    # Clone repo first (needed for Caddyfile and Dockerfile)
    clone_repo
    detect_version

    # Generate configuration files
    generate_env_file
    generate_caddyfile  # Uses consolidated Caddyfile from cloned repo
    generate_systemd_service
    generate_gc_units

    # Pull or build image and configure compose
    pull_or_build_image
    patch_compose_for_caddyfile
    patch_compose_for_tls_certs
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

cmd_update() {
    log "Updating magpie..."

    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
    verify_installation
    load_existing_config

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
    log "Updating consolidated Caddyfile and environment configuration..."
    if [[ ! -f "${INSTALL_DIR}/repo/Caddyfile" ]]; then
        die "Consolidated Caddyfile not found in repo at ${INSTALL_DIR}/repo/Caddyfile"
    fi

    # Load existing environment (includes TLS_MODE, DOMAIN, TRUSTED_PROXIES from .env)
    # These values are persisted during install by generate_env_file().
    # See issues #340 and #344 for background.
    if [[ -f "${INSTALL_DIR}/etc/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "${INSTALL_DIR}/etc/.env"
        set +a
    fi

    # Apply defaults for vars that may be missing from old .env files (migration from pre-#345 installs)
    TLS_MODE="${TLS_MODE:-$DEFAULT_TLS_MODE}"
    TRUSTED_PROXIES="${TRUSTED_PROXIES:-$DEFAULT_TRUSTED_PROXIES}"

    # Regenerate environment file to pick up new Caddy environment variables
    # This ensures the .env file has MAGPIE_SITE_ADDRESS, MAGPIE_ENABLE_LOGGING, etc.
    generate_env_file

    # Copy the consolidated Caddyfile
    generate_caddyfile

    # Re-patch compose files for deployment
    patch_compose_for_caddyfile
    patch_compose_for_tls_certs
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

    local follow_flag=""
    [[ "$FOLLOW" == "true" ]] && follow_flag="-f"

    # shellcheck disable=SC2086
    docker compose --env-file "${INSTALL_DIR}/etc/.env" logs $follow_flag --tail="$LINES" "$@"
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

Install options (only used with 'install' command):
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
  --trusted-proxies CIDR  Trusted proxy CIDRs (default: RFC1918 ranges)
  --noninteractive        Skip interactive prompts (use defaults for all config)
  --force                 Overwrite existing installation
  --from-source           Build image from source instead of pulling from ghcr.io

Update options:
  --from-source           Rebuild image from source instead of pulling from ghcr.io

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

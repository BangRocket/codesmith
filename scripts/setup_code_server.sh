#!/bin/bash
# Setup script for code-server with Cloudflare DNS and Caddy reverse proxy
#
# This script:
# 1. Installs code-server
# 2. Installs Caddy
# 3. Creates a wildcard DNS record in Cloudflare
# 4. Configures Caddy for wildcard subdomain routing with automatic TLS
#
# Prerequisites:
# - Root access
# - .env file with Cloudflare credentials
# - Domain managed by Cloudflare
#
# Usage:
#   sudo ./scripts/setup_code_server.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root (use sudo)"
   exit 1
fi

# Load environment variables
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_DIR/.env" ]]; then
    log_info "Loading .env file..."
    set -a
    source "$PROJECT_DIR/.env"
    set +a
else
    log_error ".env file not found at $PROJECT_DIR/.env"
    log_error "Copy .env.example to .env and fill in the values"
    exit 1
fi

# Validate required variables
REQUIRED_VARS=(
    "CLOUDFLARE_API_TOKEN"
    "CLOUDFLARE_ZONE_ID"
    "CLOUDFLARE_DOMAIN"
    "CODE_SERVER_DOMAIN"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!var}" ]]; then
        log_error "Missing required variable: $var"
        exit 1
    fi
done

# Get VPS public IP
log_info "Detecting public IP..."
VPS_IP=$(curl -s https://api.ipify.org || curl -s https://ifconfig.me)
if [[ -z "$VPS_IP" ]]; then
    log_error "Could not detect public IP"
    exit 1
fi
log_info "Public IP: $VPS_IP"

# Install code-server
install_code_server() {
    log_info "Installing code-server..."

    if command -v code-server &> /dev/null; then
        log_info "code-server already installed: $(code-server --version)"
        return 0
    fi

    curl -fsSL https://code-server.dev/install.sh | sh

    log_info "code-server installed successfully"
}

# Install Caddy
install_caddy() {
    log_info "Installing Caddy..."

    if command -v caddy &> /dev/null; then
        log_info "Caddy already installed: $(caddy version)"
        return 0
    fi

    # Install Caddy with Cloudflare DNS plugin for wildcard certs
    apt-get update
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-xcaddy-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-xcaddy.list

    apt-get update
    apt-get install -y xcaddy

    # Build Caddy with Cloudflare DNS module
    log_info "Building Caddy with Cloudflare DNS module..."
    xcaddy build --with github.com/caddy-dns/cloudflare --output /usr/bin/caddy

    # Create caddy user and group if they don't exist
    if ! id -u caddy &>/dev/null; then
        groupadd --system caddy
        useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
    fi

    # Create systemd service
    cat > /etc/systemd/system/caddy.service << 'EOF'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

    mkdir -p /etc/caddy

    log_info "Caddy installed successfully"
}

# Setup Cloudflare wildcard DNS
setup_cloudflare_dns() {
    log_info "Setting up Cloudflare wildcard DNS..."

    # Check if wildcard record already exists
    EXISTING=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?name=*.${CODE_SERVER_DOMAIN}" \
        -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
        -H "Content-Type: application/json" | jq -r '.result[0].id // empty')

    if [[ -n "$EXISTING" ]]; then
        log_info "Updating existing wildcard DNS record..."
        curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${EXISTING}" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"*.${CODE_SERVER_DOMAIN}\",\"content\":\"${VPS_IP}\",\"ttl\":1,\"proxied\":false}" \
            | jq -r '.success'
    else
        log_info "Creating wildcard DNS record: *.${CODE_SERVER_DOMAIN} -> ${VPS_IP}"
        curl -s -X POST "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"*.${CODE_SERVER_DOMAIN}\",\"content\":\"${VPS_IP}\",\"ttl\":1,\"proxied\":false}" \
            | jq -r '.success'
    fi

    # Also create base domain record if it doesn't exist
    EXISTING_BASE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?name=${CODE_SERVER_DOMAIN}" \
        -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
        -H "Content-Type: application/json" | jq -r '.result[0].id // empty')

    if [[ -z "$EXISTING_BASE" ]]; then
        log_info "Creating base domain DNS record: ${CODE_SERVER_DOMAIN} -> ${VPS_IP}"
        curl -s -X POST "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"${CODE_SERVER_DOMAIN}\",\"content\":\"${VPS_IP}\",\"ttl\":1,\"proxied\":false}" \
            | jq -r '.success'
    fi

    log_info "DNS records configured"
}

# Configure Caddy
configure_caddy() {
    log_info "Configuring Caddy..."

    # Create Caddyfile with wildcard subdomain routing
    cat > /etc/caddy/Caddyfile << EOF
# Global options
{
    email admin@${CLOUDFLARE_DOMAIN}
}

# Wildcard subdomain for code-server
*.${CODE_SERVER_DOMAIN} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }

    # Extract port from subdomain (e.g., 9001.code.example.com -> 9001)
    @validport expression {http.request.host.labels.0}.matches("^9[0-9]{3}\$")

    handle @validport {
        reverse_proxy localhost:{http.request.host.labels.0}
    }

    handle {
        respond "Invalid port" 404
    }
}

# Base domain - simple landing page
${CODE_SERVER_DOMAIN} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }

    respond "Codesmith Code Server" 200
}
EOF

    # Create environment file for Caddy
    mkdir -p /etc/caddy
    cat > /etc/caddy/caddy.env << EOF
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}
EOF
    chmod 600 /etc/caddy/caddy.env

    # Update systemd service to load environment
    mkdir -p /etc/systemd/system/caddy.service.d
    cat > /etc/systemd/system/caddy.service.d/override.conf << EOF
[Service]
EnvironmentFile=/etc/caddy/caddy.env
EOF

    log_info "Caddy configured"
}

# Start services
start_services() {
    log_info "Starting services..."

    systemctl daemon-reload
    systemctl enable caddy
    systemctl restart caddy

    # Wait for Caddy to start
    sleep 2

    if systemctl is-active --quiet caddy; then
        log_info "Caddy is running"
    else
        log_error "Caddy failed to start"
        journalctl -u caddy --no-pager -n 20
        exit 1
    fi
}

# Main
main() {
    log_info "=== Codesmith Code-Server Setup ==="
    log_info "Domain: ${CODE_SERVER_DOMAIN}"
    log_info "Port range: 9000-9999"

    # Check for jq
    if ! command -v jq &> /dev/null; then
        log_info "Installing jq..."
        apt-get update && apt-get install -y jq
    fi

    install_code_server
    install_caddy
    setup_cloudflare_dns
    configure_caddy
    start_services

    log_info "=== Setup Complete ==="
    log_info ""
    log_info "Code-server will be available at:"
    log_info "  https://<port>.${CODE_SERVER_DOMAIN}"
    log_info ""
    log_info "Example: https://9001.${CODE_SERVER_DOMAIN}"
    log_info ""
    log_info "Note: It may take a few minutes for DNS to propagate"
    log_info "and for TLS certificates to be issued."
}

main "$@"

#!/bin/bash
# Setup script for code-server with Cloudflare Tunnel
#
# This script:
# 1. Installs code-server
# 2. Installs Caddy (for local subdomain→port routing)
# 3. Installs and configures cloudflared tunnel
# 4. Creates DNS records via Cloudflare API
#
# Prerequisites:
# - Root access
# - .env file with Cloudflare credentials
# - Domain managed by Cloudflare
#
# Usage:
#   sudo ./scripts/setup_code_server.sh

set -e

# Ensure all standard paths are available
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:$PATH"

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
    "CLOUDFLARE_TUNNEL_TOKEN"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!var}" ]]; then
        log_error "Missing required variable: $var"
        if [[ "$var" == "CLOUDFLARE_TUNNEL_TOKEN" ]]; then
            log_error ""
            log_error "To get a tunnel token:"
            log_error "1. Go to Cloudflare Zero Trust dashboard"
            log_error "2. Navigate to Access → Tunnels"
            log_error "3. Create a tunnel named 'codesmith'"
            log_error "4. Copy the tunnel token"
        fi
        exit 1
    fi
done

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

# Install Caddy (for local routing only)
install_caddy() {
    log_info "Installing Caddy..."

    if command -v caddy &> /dev/null; then
        log_info "Caddy already installed: $(caddy version)"
        return 0
    fi

    # Install Caddy from official repo (no cloudflare module needed)
    apt-get update
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list

    apt-get update
    apt-get install -y caddy

    log_info "Caddy installed successfully"
}

# Install cloudflared
install_cloudflared() {
    log_info "Installing cloudflared..."

    if command -v cloudflared &> /dev/null; then
        log_info "cloudflared already installed: $(cloudflared --version)"
        return 0
    fi

    # Install cloudflared
    curl -L --output /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb

    log_info "cloudflared installed successfully"
}

# Configure Caddy for local subdomain routing
configure_caddy() {
    log_info "Configuring Caddy for local routing..."

    # Ensure config directory exists
    mkdir -p /etc/caddy

    # Create caddy user and group if they don't exist
    if ! id -u caddy &>/dev/null; then
        log_info "Creating caddy user..."
        groupadd --system caddy 2>/dev/null || true
        useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy 2>/dev/null || true
    fi

    # Ensure systemd service exists
    if [[ ! -f /etc/systemd/system/caddy.service ]]; then
        log_info "Creating Caddy systemd service..."
        cat > /etc/systemd/system/caddy.service << 'SERVICEEOF'
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
SERVICEEOF
    fi

    # Create Caddyfile - local only, cloudflared handles external traffic
    # Caddy listens on 8080 and routes based on subdomain to code-server ports
    cat > /etc/caddy/Caddyfile << 'EOF'
# Local reverse proxy for code-server instances
# cloudflared tunnels traffic here, Caddy routes to correct port

:8080 {
    # Extract port from Host header subdomain (e.g., 9001.code.example.com -> 9001)
    @port9000 expression {http.request.host}.startsWith("9000.")
    @port9001 expression {http.request.host}.startsWith("9001.")
    @port9002 expression {http.request.host}.startsWith("9002.")
    @port9003 expression {http.request.host}.startsWith("9003.")
    @port9004 expression {http.request.host}.startsWith("9004.")
    @port9005 expression {http.request.host}.startsWith("9005.")
    @port9006 expression {http.request.host}.startsWith("9006.")
    @port9007 expression {http.request.host}.startsWith("9007.")
    @port9008 expression {http.request.host}.startsWith("9008.")
    @port9009 expression {http.request.host}.startsWith("9009.")

    handle @port9000 { reverse_proxy localhost:9000 }
    handle @port9001 { reverse_proxy localhost:9001 }
    handle @port9002 { reverse_proxy localhost:9002 }
    handle @port9003 { reverse_proxy localhost:9003 }
    handle @port9004 { reverse_proxy localhost:9004 }
    handle @port9005 { reverse_proxy localhost:9005 }
    handle @port9006 { reverse_proxy localhost:9006 }
    handle @port9007 { reverse_proxy localhost:9007 }
    handle @port9008 { reverse_proxy localhost:9008 }
    handle @port9009 { reverse_proxy localhost:9009 }

    # Fallback - try to extract port dynamically
    handle {
        # Default response if no port matches
        respond "Invalid code-server port. Use format: 9XXX.{$CODE_SERVER_DOMAIN}" 404
    }
}
EOF

    # Replace placeholder with actual domain
    sed -i "s/{\$CODE_SERVER_DOMAIN}/${CODE_SERVER_DOMAIN}/g" /etc/caddy/Caddyfile

    log_info "Caddy configured for local routing on port 8080"
}

# Configure cloudflared tunnel
configure_cloudflared() {
    log_info "Configuring cloudflared tunnel..."

    # Check if service is already installed
    if [[ -f /etc/systemd/system/cloudflared.service ]]; then
        log_info "cloudflared service already installed"
        log_info "To add code-server routing, edit /etc/cloudflared/config.yml"
        log_info "Add this ingress rule:"
        log_info "  - hostname: \"*.${CODE_SERVER_DOMAIN}\""
        log_info "    service: http://localhost:8080"
        log_info ""
        log_info "Then restart: sudo systemctl restart cloudflared"
        return 0
    fi

    # Create config directory
    mkdir -p /etc/cloudflared

    # Install tunnel as service using token
    log_info "Installing cloudflared service with tunnel token..."
    cloudflared service install "${CLOUDFLARE_TUNNEL_TOKEN}"

    log_info "cloudflared configured"
}

# Setup DNS for tunnel (CNAME to tunnel)
setup_dns() {
    log_info "Setting up DNS records..."

    # Get tunnel ID from token (first part before the dot)
    # Actually, with connector tokens, DNS is auto-configured by Cloudflare
    # We just need to ensure the wildcard CNAME exists

    log_info "DNS will be automatically configured by the tunnel"
    log_info "If you need manual DNS setup, create a CNAME:"
    log_info "  *.${CODE_SERVER_DOMAIN} -> <tunnel-id>.cfargotunnel.com"
}

# Start services
start_services() {
    log_info "Starting services..."

    systemctl daemon-reload

    # Start Caddy
    systemctl enable caddy
    systemctl restart caddy

    # cloudflared service is started by 'cloudflared service install'
    # Just ensure it's running
    if systemctl is-active --quiet cloudflared; then
        log_info "cloudflared is running"
    else
        log_warn "cloudflared may need manual start or token verification"
        systemctl status cloudflared --no-pager || true
    fi

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
    log_info "=== Codesmith Code-Server Setup (Cloudflare Tunnel) ==="
    log_info "Domain: ${CODE_SERVER_DOMAIN}"
    log_info "Port range: 9000-9009"

    # Check for required tools
    if ! command -v curl &> /dev/null; then
        apt-get update && apt-get install -y curl
    fi

    install_code_server
    install_caddy
    install_cloudflared
    configure_caddy
    configure_cloudflared
    setup_dns
    start_services

    log_info "=== Setup Complete ==="
    log_info ""
    log_info "Code-server will be available at:"
    log_info "  https://<port>.${CODE_SERVER_DOMAIN}"
    log_info ""
    log_info "Example: https://9001.${CODE_SERVER_DOMAIN}"
    log_info ""
    log_info "Architecture:"
    log_info "  Internet → Cloudflare → cloudflared tunnel → Caddy:8080 → code-server:9xxx"
    log_info ""
    log_info "Note: No firewall ports need to be opened!"
}

main "$@"

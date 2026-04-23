#!/bin/bash
# Atlas v0.2 VPS Deployment Script
# Deploys Atlas with pgvector on fresh Ubuntu 22.04 VPS
# Usage: bash deploy_vps_v2.sh

set -e

echo "================================"
echo "Atlas v0.2 VPS Deployment"
echo "================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

# ─── STEP 1: Update System ────────────────────────────────────────────────────

echo -e "${YELLOW}[1/8] Updating system packages...${NC}"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl wget git build-essential

# ─── STEP 2: Install Docker ───────────────────────────────────────────────────

if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[2/8] Installing Docker...${NC}"
    curl -fsSL https://get.docker.com -o get-docker.sh
    bash get-docker.sh
    rm get-docker.sh
    
    # Start Docker daemon
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✓ Docker installed${NC}"
else
    echo -e "${GREEN}[2/8] Docker already installed${NC}"
fi

# ─── STEP 3: Install Docker Compose ──────────────────────────────────────────

if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}[3/8] Installing Docker Compose...${NC}"
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✓ Docker Compose installed${NC}"
else
    echo -e "${GREEN}[3/8] Docker Compose already installed${NC}"
fi

# ─── STEP 4: Create working directory ─────────────────────────────────────────

ATLAS_DIR="/home/atlas"
if [ ! -d "$ATLAS_DIR" ]; then
    echo -e "${YELLOW}[4/8] Creating Atlas directory...${NC}"
    mkdir -p "$ATLAS_DIR"
    cd "$ATLAS_DIR"
else
    echo -e "${GREEN}[4/8] Atlas directory exists${NC}"
    cd "$ATLAS_DIR"
fi

# ─── STEP 5: Clone repository ────────────────────────────────────────────────

if [ ! -d "$ATLAS_DIR/atlass" ]; then
    echo -e "${YELLOW}[5/8] Cloning Atlas repository...${NC}"
    git clone https://github.com/DukeVTI/atlass.git
    cd atlass
    echo -e "${GREEN}✓ Repository cloned${NC}"
else
    echo -e "${YELLOW}[5/8] Repository exists, pulling latest...${NC}"
    cd atlass
    git pull origin main
    echo -e "${GREEN}✓ Repository updated${NC}"
fi

# ─── STEP 6: Create .env file ────────────────────────────────────────────────

echo -e "${YELLOW}[6/8] Setting up .env file...${NC}"

if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env 2>/dev/null || {
        # If no .env.example, create minimal one
        cat > .env << 'EOF'
# PostgreSQL
POSTGRES_PASSWORD=change_me_before_deploy

# Redis
REDIS_PASSWORD=redis_secure_pass

# Anthropic API
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ALLOWED_USER_IDS=123456789

# PC Worker
PC_WORKER_SECRET=your_pc_worker_secret_here

# Logging
LOG_LEVEL=INFO
EOF
    }
    echo -e "${YELLOW}⚠ Please edit .env with your secrets:${NC}"
    echo "   nano $ATLAS_DIR/atlass/.env"
    echo ""
    echo "   Required values:"
    echo "   - POSTGRES_PASSWORD: Strong password for PostgreSQL"
    echo "   - ANTHROPIC_API_KEY: Your Anthropic API key"
    echo "   - TELEGRAM_BOT_TOKEN: Your Telegram bot token"
    echo "   - ALLOWED_USER_IDS: Your Telegram user ID"
    echo ""
    exit 0
else
    echo -e "${GREEN}✓ .env file exists${NC}"
fi

# ─── STEP 7: Create volumes and initialize PostgreSQL ──────────────────────────

echo -e "${YELLOW}[7/8] Initializing Docker volumes...${NC}"

# Create data directories if needed
mkdir -p postgres_data redis_data chroma_data

# Ensure proper permissions
chmod 755 postgres_data redis_data chroma_data

echo -e "${GREEN}✓ Volumes ready${NC}"

# ─── STEP 8: Deploy services ────────────────────────────────────────────────

echo -e "${YELLOW}[8/8] Starting Atlas services...${NC}"

# Use both docker-compose files (base + prod overrides)
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Wait for services to initialize
echo ""
echo -e "${YELLOW}Waiting for services to initialize (15s)...${NC}"
sleep 15

# ─── STEP 9: Health checks ───────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}Checking service health...${NC}"
docker compose ps

echo ""
echo -e "${YELLOW}Memory service logs (last 50 lines):${NC}"
docker compose logs memory --tail 50

# ─── Completion ───────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}✅ Atlas v0.2 Deployment Complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "Next steps:"
echo "1. Verify all services are healthy: docker compose ps"
echo "2. Test memory endpoint: curl http://localhost:8002/health"
echo "3. Send test message to bot: /start in Telegram"
echo "4. Monitor logs: docker compose logs -f"
echo ""
echo "Directory: $ATLAS_DIR/atlass"
echo "Logs: docker compose logs [service_name]"

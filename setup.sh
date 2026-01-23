#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "============================================"
echo -e "${GREEN}Starting Django Docker setup...${NC}"
echo -e "============================================"
# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! command -v docker compose &> /dev/null; then
    echo -e "${RED}Error: Docker Compose not found. Please install it first.${NC}"
    exit 1
fi

# Detect compose command
if command -v docker &> /dev/null && docker compose version &> /dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

# Step 1: Create .env from .env.dist (check if exists first)
echo -e "============================================"
echo -e "${YELLOW}[Step 1]: Setting up .env file...${NC}"
echo -e "============================================"
if [ -f ".env" ]; then
    echo -e "${YELLOW}.env already present${NC}"
else
    cp .env.dist .env
    echo -e "${GREEN}✓ .env created from .env.dist${NC}"
fi

# Step 2: Stop if running
echo -e "============================================================"
echo -e "${YELLOW}[Step 2]: Checking if services running...${NC}"
echo -e "============================================================="
if $COMPOSE ps | grep -q "Up"; then
    echo -e "${YELLOW}Services running, stopping...${NC}"
    $COMPOSE down
    echo -e "${GREEN}✓ Services stopped${NC}"
fi

# Step 3: Check and run migrations only if needed
echo -e "==========================================================="
echo -e "${YELLOW}[Step 3]: Checking for pending migrations...${NC}"
echo -e "============================================================"
if $COMPOSE run --rm web python ./manage.py showmigrations | grep -q "\[ \]"; then
    echo -e "${YELLOW}Pending migrations found, running...${NC}"
    $COMPOSE run --rm web python ./manage.py makemigrations
    $COMPOSE run --rm web python ./manage.py migrate
    echo -e "${GREEN}✓ Migrations applied${NC}"
else
    echo -e "${GREEN}✓ No pending migrations${NC}"
fi

# Step 4: Build (no cache for fresh build)
echo -e "============================================================"
echo -e "${YELLOW}[Step 4]: Building images...${NC}"
echo -e "============================================================"
$COMPOSE build
echo -e "${GREEN}✓ Build completed${NC}"

# Step 5: Start services
echo -e "============================================================"
echo -e "${YELLOW}[Step 5]: Starting application...${NC}"
echo -e "============================================================"
$COMPOSE up -d
echo -e "============================================================"
echo -e "${GREEN}✓ Application started!🥳${NC}"
echo -e "============================================================"

echo -e "${YELLOW}Commands:${NC}"
echo "  Logs: $COMPOSE logs -f"
echo "  Stop: $COMPOSE down"
echo "  Status: $COMPOSE ps"

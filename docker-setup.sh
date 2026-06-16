#!/bin/bash

# Job Finder Docker Setup Script
# ==============================
# This script helps set up the Docker environment with proper Claude OAuth authentication

set -e

echo "🚀 Job Finder Docker Setup"
echo "=========================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Claude CLI is installed
if ! command -v claude &> /dev/null; then
    echo -e "${RED}❌ Claude CLI not found${NC}"
    echo ""
    echo "Please install the Claude CLI first:"
    echo "  https://github.com/anthropics/claude-cli"
    echo ""
    echo "Installation steps:"
    echo "  1. Download from: https://github.com/anthropics/claude-cli/releases"
    echo "  2. Extract and add to your PATH"
    echo "  3. Run: claude login"
    exit 1
fi

echo -e "${GREEN}✓ Claude CLI found${NC}"

# Check if Claude credentials exist
if [ ! -d "$HOME/.claude" ]; then
    echo -e "${YELLOW}⚠️  Claude credentials not found at $HOME/.claude${NC}"
    echo ""
    echo "Run the following to authenticate with Claude:"
    echo "  claude login"
    echo ""
    exit 1
fi

echo -e "${GREEN}✓ Claude credentials found at $HOME/.claude${NC}"

# Copy .env.example to .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env${NC}"
else
    echo -e "${YELLOW}ℹ️  .env already exists, skipping copy${NC}"
fi

# Create local .claude directory for Docker (if using local copy)
echo ""
echo "Setting up Claude credentials for Docker..."

# Check if CLAUDE_CONFIG_DIR is set in .env
CLAUDE_CONFIG_DIR=$(grep -E '^CLAUDE_CONFIG_DIR=' .env | cut -d '=' -f 2 | tr -d ' ')

if [ -z "$CLAUDE_CONFIG_DIR" ]; then
    CLAUDE_CONFIG_DIR="./.claude"
fi

# If using local copy (default), copy credentials
if [ "$CLAUDE_CONFIG_DIR" = "./.claude" ]; then
    if [ ! -d "./.claude" ]; then
        echo "Copying Claude credentials to ./.claude..."
        cp -r "$HOME/.claude" "./.claude"
        echo -e "${GREEN}✓ Claude credentials copied to ./.claude${NC}"
    else
        echo -e "${YELLOW}ℹ️  ./.claude already exists${NC}"
        read -p "Update from $HOME/.claude? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cp -r "$HOME/.claude" "./.claude"
            echo -e "${GREEN}✓ Claude credentials updated${NC}"
        fi
    fi
elif [ "$CLAUDE_CONFIG_DIR" = "$HOME/.claude" ]; then
    echo -e "${GREEN}✓ Using $HOME/.claude (will be mounted directly)${NC}"
else
    echo -e "${YELLOW}⚠️  CLAUDE_CONFIG_DIR is set to: $CLAUDE_CONFIG_DIR${NC}"
    if [ ! -d "$CLAUDE_CONFIG_DIR" ]; then
        echo -e "${RED}❌ Directory does not exist: $CLAUDE_CONFIG_DIR${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Using custom credentials directory${NC}"
fi

# Verify Docker is installed
echo ""
echo "Checking Docker installation..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found${NC}"
    echo "Please install Docker: https://www.docker.com/products/docker-desktop"
    exit 1
fi
echo -e "${GREEN}✓ Docker found${NC}"

# Check Docker Compose
if ! docker compose version &> /dev/null && ! docker-compose --version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose not found${NC}"
    echo "Please install Docker Compose"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose found${NC}"

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Review and customize .env file if needed"
echo "2. Build and start the services:"
echo ""
echo "   docker compose build"
echo "   docker compose up -d"
echo ""
echo "3. View logs:"
echo "   docker compose logs -f"
echo ""
echo "4. Access the application:"
echo "   Frontend:  http://localhost:5173"
echo "   Backend:   http://localhost:8000"
echo ""
echo "To stop the services:"
echo "   docker compose down"
echo ""
echo "For more information, see the README.md and CLAUDE.md files."

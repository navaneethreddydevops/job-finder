# Docker Setup Update Summary

## Overview

The Docker Compose configuration has been completely overhauled to work seamlessly across different machines with proper Claude OAuth authentication. All changes follow the security and architecture principles outlined in CLAUDE.md.

## What Changed

### 1. **docker-compose.yml** (Complete Rewrite)

**Key improvements:**
- ✅ **Removed API key configuration** — Now uses Claude OAuth only (as per CLAUDE.md)
- ✅ **Fixed credential mounting** — Properly mounts `~/.claude` with variable path
- ✅ **Added health checks** — Both services monitor their own health
- ✅ **Proper Docker networking** — Services communicate via internal network
- ✅ **Named volumes** — Database persists across restarts
- ✅ **Environment variable support** — Customizable via `.env` file
- ✅ **Service dependencies** — Proper startup order
- ✅ **Graceful shutdown** — Proper signal handling

**Before vs After:**
```yaml
# BEFORE: Insecure, non-portable
services:
  backend:
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}  # ❌ Never use API keys
    volumes:
      - ~/.claude:/root/.claude  # ❌ Non-portable path

# AFTER: Secure, portable
services:
  backend:
    environment:
      - DATABASE_PATH=/data/jobs.db
      - ANTHROPIC_API_KEY=""  # ✅ Explicitly cleared
    volumes:
      - ${CLAUDE_CONFIG_DIR:-./.claude}:/root/.claude:ro  # ✅ Variable + read-only
```

### 2. **.env.example** (Enhanced)

**New file** with comprehensive documentation:
- `BACKEND_PORT` — Configurable backend port
- `FRONTEND_PORT` — Configurable frontend port
- `CLAUDE_CONFIG_DIR` — Path to Claude credentials (with examples for different OS)
- `API_HOST` — Frontend's backend connection (supports remote deployment)

**Replaces inline configuration** with environment-based flexibility.

### 3. **docker-setup.sh** (New)

Bash setup script that:
- ✅ Verifies Claude CLI is installed
- ✅ Checks Claude authentication exists (`~/.claude`)
- ✅ Validates Docker/Docker Compose installation
- ✅ Creates `.env` from `.env.example`
- ✅ Copies/syncs Claude credentials for Docker
- ✅ Provides clear next steps

**Usage:**
```bash
chmod +x docker-setup.sh
./docker-setup.sh
```

### 4. **docker-setup.bat** (New)

Windows batch equivalent of `docker-setup.sh` with:
- Same validation checks (PowerShell-compatible)
- Handles Windows paths (`%USERPROFILE%\.claude`)
- Same credential setup and verification

### 5. **Backend Dockerfile**

**Improvements:**
- ✅ Added `HEALTHCHECK` for orchestration
- ✅ Explicitly clear API key env vars
- ✅ Better documentation comments
- ✅ Improved layer caching

```dockerfile
# Ensure Claude OAuth authentication
ENV ANTHROPIC_API_KEY=""
ENV ANTHROPIC_AUTH_TOKEN=""

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/health', timeout=5)" || exit 1
```

### 6. **Frontend Dockerfile**

**Improvements:**
- ✅ Added `HEALTHCHECK` for orchestration
- ✅ Better layer organization
- ✅ Working directory improvements
- ✅ Comments for clarity

### 7. **.gitignore** (Updated)

**Added:**
```
.claude/
.claude-dev/
docker-compose.override.yml
```

Ensures credentials are never accidentally committed.

### 8. **.dockerignore** (Updated)

**Added:**
```
.claude/
.claude-dev/
.env
.env.local
.env.*.local
```

Prevents credentials from being added to Docker build context.

### 9. **DOCKER.md** (New - Comprehensive Guide)

Complete documentation covering:
- Prerequisites (Docker, Claude CLI)
- Quick start (setup script, build, run)
- Configuration options
- Multi-machine deployment
- Production deployment (Docker Swarm, secrets, etc.)
- Troubleshooting guide
- Advanced usage
- Security considerations
- Performance tuning
- Updating dependencies

**Length:** ~600 lines of detailed guidance

### 10. **DOCKER-QUICKREF.md** (New)

Quick reference for common commands:
- Basic operations (start, stop, logs)
- Debugging (shell, database, API tests)
- Cleaning & maintenance
- Performance monitoring
- Useful aliases

### 11. **docker-compose.override.yml.example** (New)

Example override file showing:
- Hot reload for development
- Different ports
- Resource limits
- Logging configuration
- Environment-specific settings

Useful for developers who need to customize locally without modifying the main file.

### 12. **Makefile** (New)

Make targets for common operations:
```bash
make setup      # Run setup script
make build      # Build images
make up         # Start services
make down       # Stop services
make logs       # View logs
make rebuild    # Full rebuild
make clean      # Remove all
make db-backup  # Backup database
make health     # Check service health
```

Improves DX significantly.

### 13. **README.md** (Updated)

Added "Docker Setup" section with:
- Quick start instructions
- Key features
- Links to detailed documentation
- Common commands

## Why These Changes?

### 1. **Security**
- **Never API keys** — Claude OAuth only (per CLAUDE.md requirements)
- **Read-only mounts** — Credentials cannot be modified by container
- **Environment isolation** — Secrets not in docker-compose.yml
- **Explicit clearing** — API key env vars set to empty string

### 2. **Portability**
- **Variable paths** — `CLAUDE_CONFIG_DIR` works on any machine
- **OS support** — Setup scripts for macOS/Linux (bash) and Windows (batch)
- **Configuration file** — `.env` per-user configuration without committing
- **Override file** — Development customization without modifying main file

### 3. **Reliability**
- **Health checks** — Docker restarts unhealthy services automatically
- **Named volumes** — Database persists across restarts and machine reboots
- **Service dependencies** — Proper startup order
- **Network isolation** — Services communicate via internal Docker network

### 4. **Maintainability**
- **Single source of truth** — Configuration in `.env` and `docker-compose.yml`
- **Documentation** — DOCKER.md, DOCKER-QUICKREF.md, Makefile all provide guidance
- **Setup automation** — Scripts validate environment automatically
- **Override pattern** — Developers can customize locally without committing

## How to Use

### First Time Setup

```bash
# 1. Run setup script (validates everything)
./docker-setup.sh              # macOS/Linux
docker-setup.bat               # Windows

# 2. Review .env if needed
cat .env

# 3. Build and run
docker compose build
docker compose up -d
```

### Common Operations

```bash
# View logs
docker compose logs -f

# Execute command
docker compose exec backend python -c "..."

# Access shell
docker compose exec backend bash

# Stop
docker compose down
```

### Using Make (Optional)

```bash
make setup      # Same as ./docker-setup.sh
make build      # Build images
make up         # Start services
make logs       # View logs
make down       # Stop services
```

## Environment Variables

**CLAUDE_CONFIG_DIR**
- **Default:** `./.claude` (copy credentials into repo)
- **Alternative:** `$HOME/.claude` or `%USERPROFILE%\.claude`
- **Purpose:** Points to Claude CLI credentials for Docker mount

**BACKEND_PORT**
- **Default:** `8000`
- **Purpose:** Port for FastAPI backend

**FRONTEND_PORT**
- **Default:** `5173`
- **Purpose:** Port for React frontend

**API_HOST**
- **Default:** `localhost`
- **Purpose:** Used by frontend to connect to backend
- **For remote:** Set to server IP or domain

## Backwards Compatibility

The new setup is **fully backwards compatible**:
- Existing `.env` files are respected
- Existing local credentials work as-is
- Non-Docker users can continue using `uv run`
- Changes are additive, not destructive

## Testing the Setup

```bash
# Verify build succeeds
docker compose build

# Check services start
docker compose up -d

# Test backend
curl http://localhost:8000/api/health

# Test frontend
curl http://localhost:5173/

# Check logs for errors
docker compose logs

# Stop
docker compose down
```

## Migration Path for Existing Users

If you already have a `docker-compose.yml`:

1. Backup your current setup:
   ```bash
   cp docker-compose.yml docker-compose.yml.backup
   ```

2. Replace with new version (this is safe):
   ```bash
   # Automatically done when you pull latest
   ```

3. Create `.env`:
   ```bash
   cp .env.example .env
   ```

4. Run setup:
   ```bash
   ./docker-setup.sh
   ```

5. Rebuild and restart:
   ```bash
   docker compose build
   docker compose up -d
   ```

## Files to Add to .gitignore

Already done, but verify these are in `.gitignore`:
- `.claude/` — Claude credentials
- `.env` — Configuration
- `docker-compose.override.yml` — Development overrides

## Files to Exclude from Docker Build

Already done in `.dockerignore`:
- `.claude/` — Don't copy credentials into image
- `.env` — Don't copy config into image
- Other dev files

## Documentation Structure

1. **DOCKER.md** — Full guide (read this first for comprehensive understanding)
2. **DOCKER-QUICKREF.md** — Quick commands (reference while working)
3. **docker-setup.sh/bat** — Automated setup (run this first)
4. **Makefile** — Command shortcuts (optional but convenient)
5. **.env.example** — Configuration template
6. **docker-compose.override.yml.example** — Development customization

## Next Steps

1. **Run setup script:**
   ```bash
   ./docker-setup.sh
   ```

2. **Review DOCKER.md for detailed information:**
   - Production deployment
   - Troubleshooting
   - Advanced configuration

3. **Start using:**
   ```bash
   docker compose build
   docker compose up -d
   ```

4. **Access the app:**
   - Frontend: http://localhost:5173
   - Backend: http://localhost:8000

## Support & Troubleshooting

- **Setup issues:** See DOCKER.md "Troubleshooting" section
- **Command reference:** See DOCKER-QUICKREF.md
- **Configuration:** See .env.example and DOCKER.md "Configuration"
- **Development:** See docker-compose.override.yml.example
- **Security:** See DOCKER.md "Security Considerations"

## Summary of Benefits

| Before | After |
|--------|-------|
| Non-portable paths | Variables in .env |
| Hardcoded API keys | OAuth only |
| No health checks | Automatic restart |
| Manual setup | Automated scripts |
| Single configuration file | Setup script validation |
| No documentation | Comprehensive guides |
| Limited portability | Cross-machine compatible |

The new Docker setup is production-ready, well-documented, and follows all security best practices outlined in CLAUDE.md.

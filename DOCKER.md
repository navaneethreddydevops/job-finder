# Docker Setup Guide for Job Finder

This guide explains how to run Job Finder in Docker with proper Claude OAuth authentication across different machines.

## Why Docker?

Docker containerization provides:
- **Consistent environment** across macOS, Linux, and Windows
- **Isolated services** (backend API, frontend, database)
- **Easy scaling** and multi-machine deployment
- **Reproducible builds** with the same dependencies everywhere

## Prerequisites

1. **Docker & Docker Compose**
   - Install [Docker Desktop](https://www.docker.com/products/docker-desktop) (includes Docker Compose)
   - Or install [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/)

2. **Claude CLI**
   - Install from: https://github.com/anthropics/claude-cli/releases
   - Run `claude login` to authenticate with your Claude account
   - This creates `~/.claude` with your OAuth credentials

3. **Git** (to clone this repository)

## Quick Start

### Step 1: Run Setup Script

**macOS/Linux:**
```bash
chmod +x docker-setup.sh
./docker-setup.sh
```

**Windows:**
```cmd
docker-setup.bat
```

The setup script will:
- ✓ Verify Claude CLI is installed and authenticated
- ✓ Check Docker/Docker Compose installation
- ✓ Create `.env` configuration file
- ✓ Copy Claude credentials for Docker use

### Step 2: Build and Run

```bash
# Build images
docker compose build

# Start services in the background
docker compose up -d

# View logs
docker compose logs -f
```

### Step 3: Access the Application

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000
- **API Documentation:** http://localhost:8000/docs (Swagger UI)

### Step 4: Stop Services

```bash
# Stop and remove containers
docker compose down

# Stop but keep containers (fast restart)
docker compose stop

# Resume stopped containers
docker compose start
```

## Configuration

### .env File

The `.env` file controls Docker behavior:

```bash
# Port assignments
BACKEND_PORT=8000          # Backend API port
FRONTEND_PORT=5173         # Frontend port

# Claude authentication directory
CLAUDE_CONFIG_DIR=./.claude  # Can also be $HOME/.claude

# Frontend API host
API_HOST=localhost         # Or your server IP/domain
```

#### CLAUDE_CONFIG_DIR Options

**Option 1: Copy credentials into repo (default)**
```bash
CLAUDE_CONFIG_DIR=./.claude
```
- Copies `~/.claude` to `./.claude` during setup
- Credentials live in repo (don't commit!)
- Add `./.claude` to `.gitignore` (already done)
- Best for single-machine development

**Option 2: Mount credentials from home directory**
```bash
CLAUDE_CONFIG_DIR=$HOME/.claude
```
- Or on Windows: `%USERPROFILE%\.claude`
- Mount home directory credentials directly
- Works across machines if credentials synced
- Better for teams with shared authentication

### Customizing Ports

Edit `.env` to use different ports:

```bash
BACKEND_PORT=9000       # Use port 9000 for backend
FRONTEND_PORT=3000      # Use port 3000 for frontend
```

Then access:
- Frontend: http://localhost:3000
- Backend: http://localhost:9000

## Deploying to Different Machines

### Same Machine, Different User

If another user on the same machine wants to run Job Finder:

```bash
# They should authenticate with Claude first
claude login

# Then run setup
./docker-setup.sh
```

### Different Machine

To run on a different machine:

1. **Option A: Copy credentials**
   ```bash
   # On source machine
   cp -r ~/.claude /path/to/backup/

   # On target machine
   cp /path/to/backup/.claude ~/.claude
   ./docker-setup.sh
   ```

2. **Option B: Share via home directory mount**
   Edit `.env` on target machine:
   ```bash
   CLAUDE_CONFIG_DIR=$HOME/.claude
   API_HOST=target-machine-ip
   ```

3. **Option C: CI/CD Pipeline**
   - Store `CLAUDE_CONFIG_DIR` as a mounted secret
   - Use Docker secrets or environment variables
   - See "Production Deployment" section

## Production Deployment

### Environment Variables

For production, use environment variables instead of `.env`:

```bash
# Set before docker compose
export BACKEND_PORT=8000
export FRONTEND_PORT=5173
export CLAUDE_CONFIG_DIR=$HOME/.claude
export API_HOST=job-finder.example.com

docker compose up -d
```

### Docker Secrets (Docker Swarm)

For sensitive data in Docker Swarm:

```bash
# Create secret from Claude credentials
docker secret create claude_config ~/.claude

# Reference in docker-compose.yml
services:
  backend:
    secrets:
      - claude_config
    environment:
      CLAUDE_CONFIG_DIR: /run/secrets/claude_config
```

### Building Multi-Stage Images

The current Dockerfiles already use multi-stage builds:
- **Backend:** Builds dependencies in one stage, runs from lighter base
- **Frontend:** Builds React in Node, serves from Nginx

This minimizes image size and improves pull/push performance.

### Nginx Configuration (Frontend)

The frontend uses Nginx with proxy settings. See `frontend/nginx.conf`:

```nginx
# Proxies /api/* to backend service
location /api {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
}
```

This allows the frontend to access the backend via Docker's internal DNS.

## Troubleshooting

### "Backend connection refused"

**Symptoms:** Frontend shows "Failed to fetch jobs"

**Solutions:**
1. Check backend is running: `docker compose ps`
2. Check logs: `docker compose logs backend`
3. Verify API_HOST in .env matches your setup
4. For remote access, use the server IP, not localhost

### "Claude credentials not found"

**Symptoms:** Backend exits with auth error

**Solutions:**
1. Run `claude login` on your machine
2. Verify `~/.claude` exists: `ls -la ~/.claude`
3. Run setup script again: `./docker-setup.sh`
4. Check `.env` CLAUDE_CONFIG_DIR points to valid directory

### "Port already in use"

**Symptoms:** Error like "address already in use :::8000"

**Solutions:**
1. Change ports in `.env`:
   ```bash
   BACKEND_PORT=9000
   FRONTEND_PORT=3001
   ```
2. Or kill existing service: `lsof -ti:8000 | xargs kill -9`

### "Permission denied" on ~/.claude mount

**Symptoms:** Backend can't read credentials

**Solutions:**
1. Check directory permissions: `ls -la ~/.claude`
2. Make readable: `chmod 700 ~/.claude`
3. Use local copy option in .env

### Docker daemon not running

**Symptoms:** "Cannot connect to Docker daemon"

**Solutions:**
1. Start Docker Desktop (macOS/Windows)
2. Start Docker service (Linux): `sudo systemctl start docker`
3. Add user to docker group (Linux): `sudo usermod -aG docker $USER`

## Advanced Usage

### View Real-Time Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend

# Last 50 lines
docker compose logs --tail=50
```

### Execute Commands in Container

```bash
# Run Python in backend
docker compose exec backend python -c "print('hello')"

# Access backend shell
docker compose exec backend bash

# Check database
docker compose exec backend sqlite3 /data/jobs.db ".tables"
```

### Rebuild After Code Changes

```bash
# Full rebuild (drop cache)
docker compose build --no-cache

# Rebuild and restart
docker compose up -d --build
```

### Health Checks

The setup includes health checks for both services:

```bash
# Check service health
docker compose ps

# Manually test backend health
curl http://localhost:8000/api/health

# View health check logs
docker inspect job-finder-backend | grep -A 5 Health
```

### Docker Network Inspection

```bash
# List networks
docker network ls

# Inspect job-finder network
docker network inspect job-finder-job-finder-net

# Services can reach each other:
# - backend:8000 (from frontend)
# - frontend:80 (from backend, if needed)
```

## Development Workflow

### Recommended: Development Setup

For active development, use the non-Docker approach:

```bash
# Terminal 1: Backend
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

### Using Docker for Integration Testing

```bash
# Start services
docker compose up -d

# Run tests
docker compose exec backend pytest

# Stop
docker compose down
```

## Security Considerations

1. **Credentials**
   - Never commit `./.claude` to git (already in .gitignore)
   - Don't pass ANTHROPIC_API_KEY via environment
   - Use OAuth-only authentication

2. **Volumes**
   - Database volume is named (persistent across restarts)
   - Credentials mounted as read-only (ro)

3. **Ports**
   - Only expose necessary ports in docker-compose.yml
   - Use firewall rules in production
   - Don't expose backend directly (use frontend proxy)

4. **Docker Build**
   - Multi-stage builds keep images smaller
   - No secrets in Dockerfile
   - Use specific base image versions

## Performance Tuning

### Database Performance

```bash
# Check database file size
docker compose exec backend du -sh /data/jobs.db

# Optimize database
docker compose exec backend sqlite3 /data/jobs.db "VACUUM;"
```

### Memory Usage

Set memory limits in docker-compose.yml:

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
```

### CPU Usage

Limit CPU in docker-compose.yml:

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '1'
```

## Updating

### Update Base Images

Edit Dockerfiles to use newer base images:

```dockerfile
# Update Python version
FROM python:3.13-slim  # was 3.12

# Update Node version
FROM node:22-alpine    # was 20

# Update Nginx
FROM nginx:latest-alpine
```

Then rebuild:

```bash
docker compose build --no-cache
docker compose up -d
```

### Update Dependencies

```bash
# Python dependencies (in backend/)
uv lock --upgrade
git add uv.lock

# Node dependencies (in frontend/)
npm update
git add package*.json
```

## Support

- **Documentation:** See `CLAUDE.md`, `README.md`, `app_spec.md`
- **Issues:** https://github.com/anthropics/claude-code/issues
- **Claude CLI:** https://github.com/anthropics/claude-cli

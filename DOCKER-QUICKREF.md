# Docker Quick Reference

Quick command reference for common Docker Compose operations.

## Initial Setup

```bash
# Run setup script (recommended)
./docker-setup.sh              # macOS/Linux
docker-setup.bat               # Windows

# Or manually:
docker compose build
```

## Running

```bash
# Start services
docker compose up -d

# Start with logs visible
docker compose up

# Stop services
docker compose down

# View logs
docker compose logs -f
docker compose logs -f backend
docker compose logs -f frontend

# Check status
docker compose ps
docker compose ps --quiet
```

## Configuration

```bash
# Edit environment
nano .env                      # Linux/macOS
notepad .env                   # Windows

# Apply changes (after editing .env)
docker compose up -d --force-recreate
```

## Building

```bash
# Rebuild from scratch
docker compose build --no-cache

# Rebuild and restart
docker compose up -d --build

# Rebuild specific service
docker compose build --no-cache backend
docker compose build --no-cache frontend
```

## Debugging

```bash
# Access backend shell
docker compose exec backend bash

# Run Python in backend
docker compose exec backend python -c "import backend.agent; print('OK')"

# Check database
docker compose exec backend sqlite3 /data/jobs.db ".tables"
docker compose exec backend sqlite3 /data/jobs.db "SELECT COUNT(*) FROM jobs;"

# Frontend logs in browser
# Check browser console: F12 or Cmd+Option+I
docker compose logs frontend | grep -i error

# Test API endpoint
curl http://localhost:8000/api/health
curl http://localhost:8000/api/status
```

## Cleaning

```bash
# Remove stopped containers
docker compose rm

# Remove volumes (CAREFUL: deletes database!)
docker volume rm job-finder-jobs-data

# Full cleanup (stops, removes, prunes)
docker compose down -v
docker image prune -a

# Clean specific service data
docker compose exec backend bash -c "rm -f /data/jobs.db"
```

## Ports & Networking

```bash
# View port mappings
docker compose ps
docker compose port backend
docker compose port frontend

# Check network
docker network ls
docker network inspect job-finder-job-finder-net

# Test backend from frontend container
docker compose exec frontend curl http://backend:8000/api/health
```

## Performance

```bash
# Check resource usage
docker stats

# View specific service stats
docker stats job-finder-backend job-finder-frontend

# Database optimization
docker compose exec backend sqlite3 /data/jobs.db "VACUUM;"
docker compose exec backend sqlite3 /data/jobs.db "PRAGMA optimize;"
```

## Troubleshooting

```bash
# Full status check
docker compose ps -a
docker compose logs

# Restart services
docker compose restart
docker compose restart backend

# Force update images
docker compose pull
docker compose up -d

# Full rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d

# Check health
docker compose ps | grep healthy
```

## Security & Credentials

```bash
# Verify credentials mounted
docker compose exec backend bash -c "ls -la ~/.claude"

# Check that API key is NOT set
docker compose exec backend bash -c "echo \$ANTHROPIC_API_KEY"  # Should be empty

# Verify auth works
docker compose exec backend bash -c "claude --version"
```

## Advanced

```bash
# Run command in background
docker compose exec -d backend python script.py

# Execute as specific user
docker compose exec -u root backend bash

# View compose file being used
docker compose config

# Dry run (show what would happen)
docker compose up --dry-run

# Scale services (not recommended for this app)
docker compose up -d --scale backend=2

# Export/backup database
docker compose exec backend bash -c "cp /data/jobs.db /data/jobs.db.backup"
docker compose cp backend:/data/jobs.db ./jobs.db.backup
```

## Development Tips

```bash
# Watch logs while working
docker compose logs -f --tail=20

# Restart after code changes
docker compose up -d --build

# Open backend shell for debugging
docker compose exec backend bash -c "python -i -c 'from backend.agent import *'"

# Test job search
docker compose exec backend python -c "
from backend.agent import run_job_finder_agent
import json
result = run_job_finder_agent('Python Developer')
print(json.dumps(result, indent=2))
"
```

## Multi-Machine Setup

```bash
# Machine A: Backup credentials
cp -r ~/.claude /path/to/backup/claude_credentials

# Machine B: Restore credentials
cp -r /path/to/backup/claude_credentials ~/.claude

# Or use environment variable
export CLAUDE_CONFIG_DIR=$HOME/.claude
docker compose up -d

# Update .env on target machine
echo "API_HOST=your-machine-ip" >> .env
docker compose up -d
```

## Production-Like Testing

```bash
# Create docker-compose.override.yml from example
cp docker-compose.override.yml.example docker-compose.override.yml

# Edit with resource limits
nano docker-compose.override.yml

# Apply overrides
docker compose up -d

# Monitor
docker stats
```

## Common Issues & Fixes

```bash
# Port already in use → Change in .env
echo "BACKEND_PORT=9000" >> .env
docker compose up -d

# Cannot connect to backend → Check network
docker network inspect job-finder-job-finder-net

# Claude auth error → Verify credentials
docker compose exec backend ls ~/.claude/
docker compose exec backend claude status

# Database locked → Stop and restart
docker compose down
docker compose up -d

# Disk space issues → Clean up
docker system prune -a

# Memory issues → Check resource limits and set them
docker compose stats
# Edit docker-compose.override.yml with memory limits
```

## Useful Aliases

Add to your shell profile (`.bashrc`, `.zshrc`):

```bash
alias dcup='docker compose up -d'
alias dcdown='docker compose down'
alias dclogs='docker compose logs -f'
alias dcps='docker compose ps'
alias dcexec='docker compose exec'
alias dcbuild='docker compose build --no-cache'
alias dcrestart='docker compose down && docker compose up -d'
```

Then use: `dcup`, `dclogs`, `dcps`, etc.

## More Information

- Full guide: See [DOCKER.md](DOCKER.md)
- Docker docs: https://docs.docker.com/compose/
- Docker troubleshooting: https://docs.docker.com/engine/reference/commandline/

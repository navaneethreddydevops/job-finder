# Docker Setup Checklist

Use this checklist to verify your Docker setup is complete and working.

## Pre-Setup Requirements

- [ ] Docker installed (`docker --version`)
- [ ] Docker Compose installed (`docker compose version`)
- [ ] Claude CLI installed (`claude --version`)
- [ ] Claude authenticated (`claude login` completed)
- [ ] `~/.claude` credentials directory exists

**Verify:** Run these commands and all should succeed:
```bash
docker --version
docker compose version
claude --version
ls -la ~/.claude
```

## Setup Execution

- [ ] Run setup script and it completes without errors:
  ```bash
  ./docker-setup.sh              # macOS/Linux
  docker-setup.bat               # Windows
  ```

- [ ] `.env` file created
- [ ] Claude credentials copied to `./.claude` or using `$HOME/.claude`
- [ ] Setup script confirms all checks passed (✓ marks)

## Configuration Review

- [ ] Reviewed `.env` file:
  ```bash
  cat .env
  ```
  
- [ ] Key values look reasonable:
  - `BACKEND_PORT=8000` or your chosen port
  - `FRONTEND_PORT=5173` or your chosen port
  - `CLAUDE_CONFIG_DIR` points to valid credentials directory
  - `API_HOST=localhost` (or your server if remote)

- [ ] No sensitive information in `.env`
- [ ] `.env` is in `.gitignore` (not committed)

## Docker Build

- [ ] Build succeeded without errors:
  ```bash
  docker compose build
  ```

- [ ] Images were created:
  ```bash
  docker image ls | grep job-finder
  ```
  Should show:
  - `job-finder-backend` or `job-finder_backend`
  - `job-finder-frontend` or `job-finder_frontend`

## Docker Startup

- [ ] Services started successfully:
  ```bash
  docker compose up -d
  ```

- [ ] All services are running:
  ```bash
  docker compose ps
  ```
  
  Should show all services with status "running":
  - `job-finder-backend`
  - `job-finder-frontend`
  - `job-finder-db-ready`

## Health Checks

- [ ] Backend responds to health check:
  ```bash
  curl http://localhost:8000/api/health
  ```
  Should return: `{"status":"ok"}` or similar

- [ ] Frontend is accessible:
  ```bash
  curl -I http://localhost:5173/
  ```
  Should return: `HTTP/1.1 200 OK` or similar

- [ ] Services marked as "healthy" in `docker compose ps`:
  ```bash
  docker compose ps
  ```
  Verify HEALTH column shows "healthy" for backend and frontend

## Frontend Access

- [ ] Frontend loads in browser:
  - Open: http://localhost:5173
  - Should see login page or dashboard
  - No errors in browser console (F12)

- [ ] Check browser console for errors:
  - Open DevTools (F12 or Cmd+Option+I)
  - Go to Console tab
  - Should be no red error messages

- [ ] Network requests work:
  - Go to Network tab
  - Try to log in or search for jobs
  - Requests should complete without 4xx or 5xx errors

## Backend Access

- [ ] API endpoints respond:
  ```bash
  curl http://localhost:8000/api/status
  ```

- [ ] OpenAPI docs accessible:
  - Open: http://localhost:8000/docs
  - Should see Swagger UI

- [ ] Backend can access Claude credentials:
  ```bash
  docker compose exec backend bash -c "ls -la ~/.claude"
  ```
  Should show credential files

## Database Verification

- [ ] Database file exists:
  ```bash
  docker compose exec backend bash -c "ls -lah /data/jobs.db"
  ```

- [ ] Database is accessible:
  ```bash
  docker compose exec backend sqlite3 /data/jobs.db ".tables"
  ```
  Should list tables: `auth_sessions`, `jobs`, `users`, etc.

## Credential Verification

- [ ] Claude credentials are properly mounted:
  ```bash
  docker compose exec backend bash -c "echo \$HOME/.claude:"
  docker compose exec backend ls ~/.claude
  ```

- [ ] API key is NOT set (important!):
  ```bash
  docker compose exec backend bash -c "echo \$ANTHROPIC_API_KEY"
  ```
  Should be empty (no output)

- [ ] Claude CLI works in container:
  ```bash
  docker compose exec backend claude --version
  ```
  Should print Claude version

## Logs Check

- [ ] No error messages in backend logs:
  ```bash
  docker compose logs backend | grep -i error
  ```
  Should return no results or only expected warnings

- [ ] No error messages in frontend logs:
  ```bash
  docker compose logs frontend | grep -i error
  ```
  Should return no results or only expected warnings

- [ ] View recent logs:
  ```bash
  docker compose logs --tail=50
  ```
  Should show startup messages without critical errors

## Docker Network

- [ ] Docker network created:
  ```bash
  docker network ls | grep job-finder
  ```

- [ ] Services on same network:
  ```bash
  docker network inspect job-finder-job-finder-net
  ```
  Should list both backend and frontend containers

- [ ] Frontend can reach backend:
  ```bash
  docker compose exec frontend wget -O- http://backend:8000/api/health
  ```
  Should return successful response

## Persistent Storage

- [ ] Volume created:
  ```bash
  docker volume ls | grep jobs-data
  ```

- [ ] Data persists after restart:
  ```bash
  # Create some data
  docker compose exec backend bash -c "sqlite3 /data/jobs.db 'SELECT COUNT(*) FROM jobs;'"
  
  # Stop services
  docker compose down
  
  # Start again
  docker compose up -d
  
  # Verify data is still there
  docker compose exec backend bash -c "sqlite3 /data/jobs.db 'SELECT COUNT(*) FROM jobs;'"
  ```
  Count should be the same

## Cross-Machine Testing

If testing on multiple machines:

- [ ] **Machine A:** Setup and verify all above checks ✓
- [ ] **Machine B:** Clone repo and run setup:
  ```bash
  ./docker-setup.sh
  docker compose build
  docker compose up -d
  ```
- [ ] **Machine B:** Verify frontend loads
- [ ] **Machine B:** Verify backend responds

## Development Setup (Optional)

If you want hot reload during development:

- [ ] Copy override file:
  ```bash
  cp docker-compose.override.yml.example docker-compose.override.yml
  ```

- [ ] Edit `docker-compose.override.yml` to mount source code volumes

- [ ] Restart with overrides:
  ```bash
  docker compose down
  docker compose up -d
  ```

- [ ] Code changes reflect in running containers

## Documentation Review

- [ ] Read DOCKER.md for detailed information ✓
- [ ] Bookmarked DOCKER-QUICKREF.md for quick commands ✓
- [ ] Understand configuration in .env.example ✓
- [ ] Know how to use Makefile (optional) ✓

## Troubleshooting

If any checks failed:

- [ ] Check DOCKER.md "Troubleshooting" section
- [ ] Review logs: `docker compose logs -f`
- [ ] Check specific service: `docker compose logs backend`
- [ ] Restart services: `docker compose down && docker compose up -d`
- [ ] Full rebuild: `docker compose build --no-cache && docker compose up -d`

## Final Verification

```bash
# Run this comprehensive check
echo "=== Docker Status ===" && \
docker compose ps && \
echo "" && \
echo "=== Backend Health ===" && \
curl -s http://localhost:8000/api/health | head -20 && \
echo "" && \
echo "=== Frontend Health ===" && \
curl -s -I http://localhost:5173/ | head -5 && \
echo "" && \
echo "=== Credentials ===" && \
docker compose exec backend bash -c "ls ~/.claude && echo 'API_KEY:' \$ANTHROPIC_API_KEY" && \
echo "" && \
echo "=== All Checks Complete ===" 
```

## Production Checklist

If deploying to production:

- [ ] Review DOCKER.md "Production Deployment" section
- [ ] Set appropriate resource limits in docker-compose.yml
- [ ] Configure logging (see docker-compose.override.yml.example)
- [ ] Use environment variables for sensitive config
- [ ] Set up proper secret management (Docker Swarm secrets)
- [ ] Configure backup strategy for database volume
- [ ] Test disaster recovery (restore from backup)
- [ ] Set up monitoring and alerting
- [ ] Configure log aggregation
- [ ] Test failover and restart scenarios

## Quick Health Check Command

Bookmark this command for quick verification:

```bash
docker compose ps && echo "---" && curl -s http://localhost:8000/api/health
```

## Support

If issues persist:

1. Check DOCKER.md Troubleshooting section
2. Review logs: `docker compose logs`
3. Check Docker system: `docker system df`
4. Clean and rebuild: `docker compose down -v && docker compose build --no-cache`
5. Check documentation links in README.md

---

## Sign-Off

Once all checks pass, you're ready to use Job Finder in Docker!

```bash
# Your Docker setup is complete when you can:
docker compose up -d              # ✓ Services start
docker compose ps                 # ✓ Shows all healthy
curl http://localhost:8000/api/health  # ✓ Returns success
curl http://localhost:5173        # ✓ Frontend loads
```

Start using the application:
- **Frontend:** http://localhost:5173
- **Backend:** http://localhost:8000
- **Docs:** http://localhost:8000/docs

Enjoy! 🚀

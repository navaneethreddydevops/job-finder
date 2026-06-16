.PHONY: help setup build up down logs restart clean test lint format

# Colors for output
CYAN := \033[0;36m
GREEN := \033[0;32m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "Job Finder - Make Commands"
	@echo "=========================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  ${CYAN}%-15s${NC} %s\n", $$1, $$2}'

setup: ## Run Docker setup (credentials, configuration)
	@echo "${CYAN}Running Docker setup...${NC}"
	@./docker-setup.sh

build: ## Build Docker images
	@echo "${CYAN}Building Docker images...${NC}"
	docker compose build

up: ## Start services
	@echo "${CYAN}Starting services...${NC}"
	docker compose up -d
	@echo "${GREEN}✓ Services started${NC}"
	@echo "Frontend:  http://localhost:5173"
	@echo "Backend:   http://localhost:8000"

down: ## Stop services
	@echo "${CYAN}Stopping services...${NC}"
	docker compose down
	@echo "${GREEN}✓ Services stopped${NC}"

logs: ## View logs
	docker compose logs -f

logs-backend: ## View backend logs
	docker compose logs -f backend

logs-frontend: ## View frontend logs
	docker compose logs -f frontend

restart: ## Restart services
	@echo "${CYAN}Restarting services...${NC}"
	docker compose restart
	@echo "${GREEN}✓ Services restarted${NC}"

ps: ## Show running services
	docker compose ps

rebuild: ## Rebuild images and restart
	@echo "${CYAN}Rebuilding and restarting...${NC}"
	docker compose down
	docker compose build --no-cache
	docker compose up -d
	@echo "${GREEN}✓ Rebuilt and restarted${NC}"

clean: ## Stop services and remove volumes
	@echo "${CYAN}Cleaning up...${NC}"
	docker compose down -v
	@echo "${GREEN}✓ Cleaned${NC}"

shell-backend: ## Open shell in backend container
	docker compose exec backend bash

shell-frontend: ## Open shell in frontend container
	docker compose exec frontend sh

db-backup: ## Backup database
	@echo "${CYAN}Backing up database...${NC}"
	docker compose exec backend bash -c "cp /data/jobs.db /data/jobs.db.backup"
	docker compose cp backend:/data/jobs.db ./jobs.db.backup
	@echo "${GREEN}✓ Database backed up to ./jobs.db.backup${NC}"

db-clean: ## Clear database (WARNING: deletes all jobs)
	@echo "${CYAN}Clearing database...${NC}"
	docker compose exec backend bash -c "rm -f /data/jobs.db"
	docker compose restart backend
	@echo "${GREEN}✓ Database cleared${NC}"

health: ## Check service health
	@echo "${CYAN}Checking service health...${NC}"
	@docker compose ps --format "table {{.Service}}\t{{.Status}}"

stats: ## Show resource usage
	docker stats

dev-setup: ## Setup for development (mount source code)
	@echo "${CYAN}Copying docker-compose.override.yml...${NC}"
	@cp docker-compose.override.yml.example docker-compose.override.yml
	@echo "${GREEN}✓ Created docker-compose.override.yml${NC}"
	@echo "Edit docker-compose.override.yml to enable hot reload"

validate: ## Validate docker-compose.yml
	@echo "${CYAN}Validating docker-compose.yml...${NC}"
	docker compose config > /dev/null && echo "${GREEN}✓ Configuration is valid${NC}"

# Backend development
backend-shell: ## Open Python shell in backend
	docker compose exec backend python

backend-test: ## Run backend tests
	docker compose exec backend python -m pytest

backend-lint: ## Lint backend code
	docker compose exec backend python -m pylint backend/

# Frontend development
frontend-shell: ## Open shell in frontend container
	shell-frontend

frontend-build: ## Build frontend production bundle
	@echo "${CYAN}Building frontend...${NC}"
	docker compose exec frontend npm run build
	@echo "${GREEN}✓ Frontend built${NC}"

# Useful for CI/CD
ci-build: build ## Alias for CI pipelines
ci-test: backend-test ## Run tests in CI
ci-up: up ## Start services in CI

.DEFAULT_GOAL := help

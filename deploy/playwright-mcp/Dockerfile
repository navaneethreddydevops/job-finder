# Playwright MCP sidecar — headless browser service for the apply agent.
# Build:  docker build -t playwright-mcp-sidecar deploy/playwright-mcp
# Run:    docker run -p 8080:8080 -e PLAYWRIGHT_MCP_TOKEN=... playwright-mcp-sidecar
FROM mcr.microsoft.com/playwright:v1.54.0-noble

WORKDIR /app

# Install @playwright/mcp at build time (so `npx` at runtime never hits the
# network) and make sure its pinned Playwright version has a matching Chromium.
RUN npm init -y >/dev/null \
    && npm install @playwright/mcp@latest \
    && npx playwright install chromium

COPY server.mjs .

ENV PORT=8080 \
    MCP_PORT=8931 \
    DATA_DIR=/data/runs
RUN mkdir -p /data/runs

EXPOSE 8080
CMD ["node", "server.mjs"]

# Playwright MCP sidecar — headless browser service for the apply agent.
# Build:  docker build -t playwright-mcp-sidecar deploy/playwright-mcp
# Run:    docker run -p 8080:8080 -e PLAYWRIGHT_MCP_TOKEN=... playwright-mcp-sidecar
FROM mcr.microsoft.com/playwright:v1.54.0-noble

WORKDIR /app

# Install @playwright/mcp at build time and a Chromium build matching ITS
# bundled Playwright (which may be newer than the base image's). server.mjs runs
# this installed copy directly (node_modules/.bin/mcp-server-playwright) so the
# runtime can never resolve a different version from the registry.
# `ws` powers the CDP screencast bridge (live browser stream); `playwright-core`
# resolves the bundled Chromium executable path we launch with a CDP debug port.
RUN npm init -y >/dev/null \
    && npm install @playwright/mcp@latest ws playwright-core \
    && npx playwright install --with-deps chromium

COPY server.mjs .

ENV PORT=8080 \
    MCP_PORT=8931 \
    DATA_DIR=/data/runs
RUN mkdir -p /data/runs

EXPOSE 8080
CMD ["node", "server.mjs"]

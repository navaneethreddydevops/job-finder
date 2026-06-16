# Stage 1: Build React frontend
FROM node:20-alpine AS build-stage
WORKDIR /app

# Copy package descriptors and install dependencies
COPY package*.json ./
RUN npm ci

# Copy code files and build the production dist bundle
COPY . .
RUN npm run build

# Stage 2: Serve compiled React pages with Nginx
FROM nginx:stable-alpine
WORKDIR /usr/share/nginx/html

# Copy built React app
COPY --from=build-stage /app/dist .

# Copy Nginx configuration for proper routing and proxying
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]

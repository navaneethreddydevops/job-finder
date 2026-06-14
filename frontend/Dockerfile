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
COPY --from=build-stage /app/dist /usr/share/nginx/html
COPY --from=build-stage /app/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

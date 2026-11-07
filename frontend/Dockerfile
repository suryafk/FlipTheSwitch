# BLACKBOX frontend. Build context: ./frontend
# Stage 1: build the static bundle.
FROM node:22.16.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Same-origin API: nginx below proxies /api to the backend container.
ENV VITE_API_BASE_URL=""
RUN npm run build

# Stage 2: serve it with nginx.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

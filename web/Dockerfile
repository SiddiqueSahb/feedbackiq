# FeedbackIQ web app - the customer-facing single-page app (Milestone 8).
#
# Build from the project root, like the other images:
#
#   docker build -f web/Dockerfile -t feedbackiq-web .
#
# Or just: docker compose up web
#
# Two stages. Node builds the static app; an unprivileged nginx serves it and forwards /api to the
# backend (web/nginx.conf). The final image holds static files and nginx - no Node, no source, no
# node_modules - and no secret: the app has no configuration of its own to hide.

# ------------------------------------------------------------------ build
FROM node:24-alpine AS build

WORKDIR /web

# Dependencies first, so this layer is reused until package-lock.json changes. `npm ci` installs
# exactly what the lock file pins; --ignore-scripts because nothing the build needs runs an install
# script, and not running third-party install scripts is the safer default.
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts

# `npm run build` type-checks, then writes dist/. .dockerignore keeps the host's node_modules and
# dist out of this copy.
COPY web/ ./
RUN npm run build

# ------------------------------------------------------------------ serve
FROM nginxinc/nginx-unprivileged:1.29-alpine

# This image already runs nginx as a non-root user on port 8080.
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

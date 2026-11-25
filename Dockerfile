# Stage 1: build the SPA
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: API + MCP + static serving
FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt ./
# The lock pins every package by version and hash; pip refuses anything else.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY api/app ./app
COPY --from=web /web/dist ./static
ENV STATIC_DIR=/app/static \
    DB_PATH=/data/ipam.db
# The server runs as ipam, uid and gid 10001, never root (spec §7.3, #173). The ids are
# fixed here, not a build argument, so the owner of a volume does not depend on the build.
# The code above stays root's, so the process cannot change it. /data is where the
# ipam-data volume mounts: a new, empty volume takes this directory's owner and mode.
# A volume an image before #173 made belongs to root and is given to ipam once, by the
# owner (README, "Upgrading from an image that ran as root"); nothing here runs as root
# at start to do it.
RUN groupadd --system --gid 10001 ipam \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin ipam \
 && install -d -m 0700 -o 10001 -g 10001 /data
USER 10001:10001
EXPOSE 8080
# uvicorn with one worker, whatever the environment says (brief §2.2), and the limits
# on request heads and idle connections that its command line cannot set (brief §4.3).
CMD ["python", "-m", "app.serve"]

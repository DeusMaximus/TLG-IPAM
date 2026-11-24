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
EXPOSE 8080
# uvicorn with one worker, whatever the environment says (brief §2.2), and the limits
# on request heads and idle connections that its command line cannot set (brief §4.3).
CMD ["python", "-m", "app.serve"]

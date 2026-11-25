# Build from the repository root: docker build -f .agents/spikes/25/Dockerfile -t tlg-ipam-spike25 .
FROM python:3.13-slim
WORKDIR /spike
COPY api/requirements.txt ./
# The server's own lock, so the spike runs the fastmcp and mcp the app runs.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY .agents/spikes/25/spike.py ./
RUN useradd --system --no-create-home spike && mkdir /logs && chown spike /logs
USER spike
ENV SPIKE_LOG=/logs/spike.jsonl PORT=8090
EXPOSE 8090
CMD ["python", "spike.py"]

# The scan agent image (v0.2 spec §5.6). Build from the repository root:
#   docker build -f agent/Dockerfile -t ghcr.io/deusmaximus/tlg-ipam-agent:dev agent
# Run with host networking and CAP_NET_RAW, as the Agents page shows.
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
# The lock pins every package by version and hash; pip refuses anything else.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src ./src
# The agent's own code is this copy, from the release; it never fetches any.
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1
# Root inside the container, so that the CAP_NET_RAW added with --cap-add NET_RAW is
# effective for raw ICMP sockets; the container gets no other added capability.
ENTRYPOINT ["python", "-m", "tlg_ipam_agent"]

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
# Where the agent stores the agent token its enrolment token is exchanged for (#22).
# Mount a named volume here (-v tlg-ipam-agent:/var/lib/tlg-ipam-agent): a new volume
# takes this directory's owner and mode, root's and 0700, and the token in it outlives
# the container, so a recreated one starts with it instead of enrolling again. There is
# no VOLUME line: an anonymous volume would be lost on recreation all the same.
RUN mkdir -m 0700 /var/lib/tlg-ipam-agent
# Root inside the container, so that CAP_NET_RAW is effective for the raw ICMP and
# ARP sockets.
# --cap-add NET_RAW adds it to the runtime's default capability set; it does not drop
# the rest (--cap-drop ALL --cap-add NET_RAW would).
ENTRYPOINT ["python", "-m", "tlg_ipam_agent"]

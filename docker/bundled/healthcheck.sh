#!/bin/sh
# Magpie bundled single-container image -- combined health check.
#
# Hits /health *through Caddy* on the container's single external port, not
# uvicorn directly. Caddy's `handle /health` block reverse_proxies to
# 127.0.0.1:8000, so this one request only returns 200 when BOTH processes
# are up:
#   - Caddy must be listening and routing for curl to get any response.
#   - uvicorn must be listening and returning 200 for the response to be
#     200 (if uvicorn is down, Caddy's reverse_proxy answers 502, not 200).
exec curl -fsS -o /dev/null http://127.0.0.1:8080/health

# Synthetic network-operations MCP server (lab construct, fictional data only).
# Build from the src folder:  az acr build -r <registry> -t supportbuddy-network-mcp:1 -f mcp_server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.24.0,<2"

COPY lab_backend ./lab_backend
COPY mcp_server ./mcp_server

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
USER nobody
CMD ["python", "-m", "mcp_server.server"]

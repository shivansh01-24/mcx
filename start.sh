#!/bin/bash
set -e

echo "Checking dependencies..."

# 1. Verify Docker is installed
if ! [ -x "$(command -v docker)" ]; then
  echo "Error: docker is not installed. Please install Docker and try again." >&2
  exit 1
fi

# 2. Verify Docker is running
if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker daemon is not running. Please start Docker and try again." >&2
  exit 1
fi

echo "Docker daemon verified. Building and starting containers..."
docker compose up --build -d

echo "Waiting for services to become healthy..."
# Poll the FastAPI health endpoint
MAX_RETRIES=30
RETRY_COUNT=0
HEALTH_URL="http://localhost/api/v1/health"

until $(curl --output /dev/null --silent --head --fail "$HEALTH_URL"); do
    RETRY_COUNT=$((RETRY_COUNT+1))
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "Error: Services did not become healthy in time. Check logs using 'docker compose logs'."
        exit 1
    fi
    echo -n "."
    sleep 2
done

echo ""
echo "================================================="
echo "MCX Gold & Silver API"
echo "================================================="
echo ""
echo "Status:"
echo "READY"
echo ""
echo "Dashboard:"
echo "http://localhost"
echo ""
echo "Swagger:"
echo "http://localhost/docs"
echo ""
echo "REST API:"
echo "http://localhost/api/v1/prices"
echo ""
echo "WebSocket:"
echo "ws://localhost/api/v1/ws"
echo ""
echo "Default API Key:"
echo "mcx_pub_dev_key"
echo ""
echo "================================================="

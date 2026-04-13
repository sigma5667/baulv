#!/bin/bash
export DOCKER_HOST=npipe:////./pipe/docker_engine
cd "$(dirname "$0")"
set -a
source .env
set +a
docker compose up --build

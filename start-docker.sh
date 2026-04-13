#!/bin/bash
export DOCKER_HOST=npipe:////./pipe/docker_engine
cd "C:/Users/somme/baulv"
source .env
exec docker compose up --build

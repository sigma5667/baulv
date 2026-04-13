# =============================================================
# BauLV — Single-container production build for Railway
# Stage 1: Build the React frontend
# Stage 2: Python backend serving API + static frontend
# =============================================================

# --- Stage 1: Build frontend ---
FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ .
RUN npm run build


# --- Stage 2: Production backend ---
FROM python:3.12-slim

WORKDIR /app

# System dependencies for PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/pyproject.toml .
RUN pip install --no-cache-dir .

# Copy backend code
COPY backend/ .

# Copy built frontend into backend static directory
COPY --from=frontend-build /frontend/dist /app/static

# Create uploads directory
RUN mkdir -p /app/uploads

# Railway injects PORT env var
ENV PORT=8000

EXPOSE ${PORT}

# Production server: gunicorn with uvicorn workers
RUN pip install --no-cache-dir gunicorn

CMD ["sh", "-c", "gunicorn app.main:app --bind 0.0.0.0:${PORT} --workers 2 --worker-class uvicorn.workers.UvicornWorker --timeout 120"]

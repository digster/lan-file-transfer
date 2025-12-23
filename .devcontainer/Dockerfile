FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Install system dependencies for Flet and Zeroconf
RUN apt-get update && apt-get install -y \
    libgtk-3-0 \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libavahi-client3 \
    avahi-daemon \
    avahi-utils \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster package management
RUN pip install uv

# Set working directory
WORKDIR /workspaces/file-transfer-claude

# Copy project files
COPY pyproject.toml ./

# Install dependencies
RUN uv sync || true



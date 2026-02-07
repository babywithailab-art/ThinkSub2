# ThinkSub2 Docker Image
# Multi-stage build with GUI support (X11) and audio device access

# Stage 1: Base image with system dependencies
FROM python:3.10-slim as base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # X11 forwarding for GUI
    x11-apps \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libxrandr2 \
    libxinerama1 \
    libxfixes3 \
    libxcursor1 \
    libxkbcommon0 \
    # Audio (ALSA/PulseAudio)
    libasound2-dev \
    libpulse-dev \
    pulseaudio \
    # FFmpeg for media processing
    ffmpeg \
    # Basic utilities
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV DISPLAY=:0 \
    PULSE_SERVER=unix:/run/user/1000/pulse/native \
    QT_QPA_PLATFORM=xcb

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

# Stage 2: Application
FROM base as app

WORKDIR /app

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app

# Create directories
RUN mkdir -p /app/logs \
    /app/projects \
    /app/models \
    /app/temp

# Set permissions
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose ports (for future web dashboard if needed)
EXPOSE 8080

# Health check (basic process check)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

# Default command (will be overridden by docker-compose)
CMD ["python", "main.py"]

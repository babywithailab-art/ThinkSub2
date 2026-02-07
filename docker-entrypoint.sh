#!/bin/bash
# ThinkSub2 Docker Entry Point
# Handles X11 forwarding, PulseAudio setup, and logging configuration

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

echo_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# X11 Forwarding Setup
setup_x11() {
    if [ -n "$DISPLAY" ]; then
        echo_info "X11 forwarding enabled: DISPLAY=$DISPLAY"

        # Check if X11 socket is mounted
        if [ ! -S "/tmp/.X11-unix/X$((DISPLAY))" ]; then
            echo_warn "X11 socket not found. GUI may not work."
            echo_warn "Ensure you're running: xhost +local:docker"
        fi

        # Set Qt platform
        export QT_QPA_PLATFORM=xcb
    else
        echo_warn "DISPLAY not set. Running in headless mode (no GUI)"
        export QT_QPA_PLATFORM=offscreen
    fi
}

# PulseAudio Setup
setup_audio() {
    if [ -d "/run/user/1000/pulse" ]; then
        echo_info "PulseAudio socket found: /run/user/1000/pulse"
        export PULSE_SERVER=unix:/run/user/1000/pulse/native

        # Test audio access
        if ! timeout 2 pactl info >/dev/null 2>&1; then
            echo_warn "Cannot connect to PulseAudio. Audio may not work."
            echo_warn "Ensure you're running: pulseaudio --load=module-native-protocol-unix"
        fi
    else
        echo_warn "PulseAudio socket not found. Audio may not work."
    fi
}

# Directory Setup
setup_directories() {
    echo_info "Setting up directories..."

    # Create logs directory with timestamp
    LOG_DIR="/app/logs"
    mkdir -p "$LOG_DIR"

    # Check if temp directory exists
    if [ ! -d "/app/temp" ]; then
        mkdir -p "/app/temp"
    fi

    # Set permissions
    chmod 755 "$LOG_DIR"
    chmod 755 "/app/temp"
}

# Logging Configuration
setup_logging() {
    echo_info "Configuring logging..."

    # Set log level from environment (default: DEBUG)
    export LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
    export LOG_FORMAT="${LOG_FORMAT:-text}"

    echo_info "LOG_LEVEL: $LOG_LEVEL"
    echo_info "LOG_FORMAT: $LOG_FORMAT"

    # Create log file with timestamp
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    export LOG_FILE="$LOG_DIR/thinksub_qa_$TIMESTAMP.log"

    echo_info "Log file: $LOG_FILE"
}

# Pre-flight Checks
preflight_check() {
    echo_info "Running pre-flight checks..."

    # Check Python
    if ! command -v python &> /dev/null; then
        echo_error "Python not found!"
        exit 1
    fi

    # Check required Python packages
    echo_info "Checking Python dependencies..."
    python -c "import sys; print('Python:', sys.version)"

    # Check FFmpeg
    if ! command -v ffmpeg &> /dev/null; then
        echo_warn "FFmpeg not found. Some features may not work."
    else
        echo_info "FFmpeg: $(ffmpeg -version | head -n1)"
    fi

    # Check GPU availability (optional)
    if command -v nvidia-smi &> /dev/null; then
        echo_info "GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)"
        export CUDA_VISIBLE_DEVICES=0
    else
        echo_info "No GPU detected or nvidia-smi not available."
    fi
}

# Main Application
run_app() {
    echo_info "Starting ThinkSub2..."
    echo_info "====================="

    # Launch application
    exec python main.py
}

# Cleanup on exit
cleanup() {
    echo_info "Shutting down..."

    # Save any pending logs
    if [ -n "$LOG_FILE" ]; then
        echo_info "Logs saved to: $LOG_FILE"
    fi

    exit 0
}

# Trap signals
trap cleanup SIGTERM SIGINT

# Main execution
echo_info "ThinkSub2 Docker Entry Point"
echo_info "=============================="

setup_x11
setup_audio
setup_directories
setup_logging
preflight_check
run_app

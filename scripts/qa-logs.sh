#!/bin/bash
# ThinkSub2 QA Log Streaming Script
# Real-time log monitoring with pattern detection and highlighting

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
SERVICE_NAME="thinksub2"
LOG_PATTERN='ERROR|WARNING|DEBUG|INFO|MODEL_READY|MODEL_ERROR'
FILTER="${1:-}"

# Statistics
STATS_ERROR_COUNT=0
STATS_WARNING_COUNT=0
STATS_START_TIME=$(date +%s)

# Functions
print_colored() {
    local color=$1
    shift
    echo -e "${color}$@${NC}"
}

print_header() {
    echo ""
    print_colored "${BOLD}${BLUE}" "╔════════════════════════════════════════╗"
    print_colored "${BOLD}${BLUE}" "║   ThinkSub2 QA Log Monitor              ║"
    print_colored "${BOLD}${BLUE}" "╚════════════════════════════════════════╝"
    echo ""
}

print_stats() {
    local duration=$(( $(date +%s) - STATS_START_TIME ))
    local minutes=$(( duration / 60 ))
    local seconds=$(( duration % 60 ))

    print_colored "${CYAN}" "─────────────────────────────────────────────────"
    print_colored "${CYAN}" "Monitoring Time: ${minutes}m ${seconds}s"
    print_colored "${GREEN}" "Errors: ${STATS_ERROR_COUNT}"
    print_colored "${YELLOW}" "Warnings: ${STATS_WARNING_COUNT}"
    print_colored "${CYAN}" "─────────────────────────────────────────────────"
}

highlight_log() {
    local line=$1

    # Apply filter if specified
    if [ -n "$FILTER" ]; then
        if ! echo "$line" | grep -qi "$FILTER"; then
            return
        fi
    fi

    # Colorize based on log pattern
    case "$line" in
        *ERROR*)
            echo -e "${RED}${line}${NC}"
            ((STATS_ERROR_COUNT++))
            # Beep on error (optional, can be disabled)
            # echo -e "\a"
            ;;
        *WARN*)
            echo -e "${YELLOW}${line}${NC}"
            ((STATS_WARNING_COUNT++))
            ;;
        *MODEL_READY*|*ready*|*started*)
            echo -e "${GREEN}${line}${NC}"
            ;;
        *DEBUG*|*DEBUG_*)
            echo -e "${MAGENTA}${line}${NC}"
            ;;
        *[Aa][Uu][Dd][Ii][Oo]*|*[Aa][Uu][Dd][Ii][Oo]*)  # AUDIO
            echo -e "${BLUE}${line}${NC}"
            ;;
        *[Tt][Rr][Aa][Nn][Ss][Cc][Rr][Ii][Bb][Ee][Rr]*)  # Transcriber
            echo -e "${CYAN}${line}${NC}"
            ;;
        *[Mm][Oo][Dd][Ee][Ll]*)  # Model
            echo -e "${BOLD}${GREEN}${line}${NC}"
            ;;
        *[Ss][Tt][Tt]*)  # STT
            echo -e "${CYAN}${line}${NC}"
            ;;
        *)
            echo "$line"
            ;;
    esac
}

# Trap for cleanup
cleanup() {
    echo ""
    print_colored "${YELLOW}" "─────────────────────────────────────────────────"
    print_colored "${YELLOW}" "Log monitoring stopped"
    print_stats
    echo ""
    exit 0
}

trap cleanup SIGINT SIGTERM

# Show usage
usage() {
    echo "Usage: $0 [filter]"
    echo ""
    echo "Options:"
    echo "  [filter]    Grep filter pattern (e.g., 'ERROR', 'transcriber', 'req_abc123')"
    echo ""
    echo "Examples:"
    echo "  $0                          # Stream all logs"
    echo "  $0 ERROR                   # Show only ERROR logs"
    echo "  $0 req_abc123              # Show logs for specific request ID"
    echo "  $0 'MODEL_READY|MODEL_ERROR'  # Show model-related events"
    echo ""
    echo "Log Levels and Colors:"
    echo "  ${RED}ERROR${NC}    - Errors and failures"
    echo "  ${YELLOW}WARNING${NC} - Warnings and issues"
    echo "  ${GREEN}INFO${NC}     - Ready/started events"
    echo "  ${MAGENTA}DEBUG${NC}   - Debug messages"
    echo "  ${BLUE}AUDIO${NC}     - Audio-related logs"
    echo "  ${CYAN}STT${NC}       - Speech-to-text events"
    echo ""
}

# Main execution
print_header

if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    usage
    exit 0
fi

echo -e "${BOLD}Monitoring Docker logs for ${SERVICE_NAME}...${NC}"
echo -e "${BOLD}Filter: ${FILTER:-<none>}${NC}"
echo -e "${BOLD}Press Ctrl+C to stop${NC}"
echo ""

# Stream logs
docker compose logs -f --tail=100 "$SERVICE_NAME" 2>&1 | while read -r line; do
    highlight_log "$line"
done

#!/bin/bash
set -e

# Function for logging
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Handle termination signals
trap 'log "Received SIGTERM or SIGINT, shutting down..."; sudo service openvswitch-switch stop; kill "$DOCKERD_PID" 2>/dev/null; exit 0' TERM INT

# Startup message
log "Starting Mininet Docker container..."

# Start Open vSwitch service
log "Starting Open vSwitch service..."
sudo service openvswitch-switch start || {
    log "ERROR: Failed to start Open vSwitch service"
    exit 1
}

# Print OVS status
log "Open vSwitch is running with the following configuration:"
sudo ovs-vsctl show

# Start the nested Docker daemon (needed for ContainerHost nodes)
log "Starting Docker daemon..."
dockerd >/var/log/dockerd.log 2>&1 &
DOCKERD_PID=$!
for _ in $(seq 1 30); do
    [ -S /var/run/docker.sock ] && break
    sleep 1
done
if [ ! -S /var/run/docker.sock ]; then
    log "ERROR: Docker daemon did not start; see /var/log/dockerd.log"
fi

# Execute the command passed to docker
log "Initialization complete"
exec "$@"
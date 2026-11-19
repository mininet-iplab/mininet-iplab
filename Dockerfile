# Dockerfile for Mininet-IPLab

# Krill (the RPKI CA Service), the same release on both architectures. NLnet
# Labs packages it for Debian on amd64 only, so amd64 installs that package
# (KRILL_PACKAGE_VERSION in the runtime stage) and arm64 copies the static
# krill and krillc, unmodified, from the official multi-arch image. Bump by
# hand: change all three values (the digest is the multi-arch index,
# `docker buildx imagetools inspect nlnetlabs/krill:<tag>`), then regenerate
# the notices with `python3 util/krill_notices.py <tag> <digest>` and commit
# licenses/krill/ in the same change.
ARG KRILL_VERSION=v0.16.1
ARG KRILL_DIGEST=sha256:cb80c99b68709cf7579e86250b4566b3a2513df995394e8a81ce89f65b8d2a32
FROM nlnetlabs/krill:${KRILL_VERSION}@${KRILL_DIGEST} AS krill-arm64
# amd64 takes nothing from the image, so BuildKit never pulls it there.
FROM debian:bookworm AS krill-amd64
FROM krill-${TARGETARCH} AS krill

# ==============================================================================
# Stage 1: Builder - Mininet compilation and dependency preparation
# ==============================================================================
FROM debian:bookworm AS builder

ARG MININET_VERSION=6eb8973c0bfd13c25c244a3871130c5e36b5fbd7
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies (will not be in final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    ca-certificates \
    python3-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Fetch the pinned Mininet (shallow, for smaller size). `git clone
# --branch` accepts only a branch or a tag, never a commit SHA — and a SHA
# is what makes the build reproducible, since a branch moves under us. This
# spelling takes a SHA, a tag or a branch alike.
RUN git init -q /opt/mininet \
    && git -C /opt/mininet remote add origin https://github.com/mininet/mininet.git \
    && git -C /opt/mininet fetch -q --depth 1 origin ${MININET_VERSION} \
    && git -C /opt/mininet checkout -q FETCH_HEAD


# ==============================================================================
# Stage 2: Production Runtime
# ==============================================================================
FROM debian:bookworm

ARG KEA_VERSION=3.2.1-isc20260928103142
# Validator Service: Routinator from NLnet Labs' apt repository (amd64 and
# arm64). Bumped by hand like Kea.
ARG ROUTINATOR_VERSION=0.16.1-1bookworm
# RPKI CA Service on amd64: Krill from the same repository, the release
# KRILL_VERSION names (see the krill stages).
ARG KRILL_PACKAGE_VERSION=0.16.1-1bookworm
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/lib/frr:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV MININET_DOCKER=true

# Metadata labels for image identification and compliance
LABEL org.opencontainers.image.title="Mininet-IPLab" \
      org.opencontainers.image.description="Mininet-IPLab with Mininet and FRR routing" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/mininet-iplab/mininet-iplab" \
      org.opencontainers.image.licenses="MIT" \
      maintainer="Mininet-IPLab Team"

# Prevent services from being started during image build (temporary)
RUN printf '%s\n' '#!/bin/sh' 'exit 101' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d

# Configure ISC's pinned Kea 3.2 repository.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    && curl -1sLf \
      https://dl.cloudsmith.io/public/isc/kea-3-2/gpg.720DD962343B440F.key \
      | gpg --dearmor -o /usr/share/keyrings/isc-kea-3-2-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/isc-kea-3-2-archive-keyring.gpg] https://dl.cloudsmith.io/public/isc/kea-3-2/deb/debian bookworm main" \
      > /etc/apt/sources.list.d/isc-kea-3-2.list \
    && curl -fsSL https://packages.nlnetlabs.nl/aptkey.asc \
      | gpg --dearmor -o /usr/share/keyrings/nlnetlabs-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/nlnetlabs-archive-keyring.gpg] https://packages.nlnetlabs.nl/linux/debian bookworm main" \
      > /etc/apt/sources.list.d/nlnetlabs.list \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies in a single layer for efficiency
# Organized by functional category for maintainability
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Python runtime and utilities
    python3 \
    python3-pip \
    python-is-python3 \
    python3-termcolor \
    python3-setuptools \
    python3-mako \
    # Core system utilities
    sudo \
    ca-certificates \
    lsb-release \
    patch \
    # Networking packages
    openvswitch-switch \
    iproute2 \
    iputils-ping \
    net-tools \
    bridge-utils \
    tcpdump \
    # Packet Capture: tshark dissects to PDML for the Web UI pane and
    # writes the pcap beside it
    tshark \
    # NAT Table: conntrack (conntrack-tools) lists a translating
    # Router's connection tracking table; the kernel here has no
    # /proc/net/nf_conntrack to read instead
    conntrack \
    traceroute \
    iperf3 \
    iptables \
    iptables-persistent \
    socat \
    dnsutils \
    # Authoritative DNS Service: Knot DNS with python bindings for control channel
    knot \
    python3-libknot \
    # Recursive Caching Resolver Service: Unbound with DNSSEC validation
    unbound \
    # DHCP Service: ISC Kea 3.2 is pinned because DHCPv6 relay support needs it.
    # The Control Agent was removed in Kea 3.2; Kea is controlled over
    # each daemon's own unix control socket directly.
    isc-kea-dhcp4=${KEA_VERSION} \
    isc-kea-dhcp6=${KEA_VERSION} \
    isc-kea-common=${KEA_VERSION} \
    isc-kea-hooks=${KEA_VERSION} \
    dhcpcd-base \
    # DHCP Relay: relay-only dnsmasq carries DHCP for Subnets a Service
    # cannot reach directly. -base is the daemon without the
    # systemd/network-manager integration, matching dhcpcd-base above.
    dnsmasq-base \
    # Validator Service: Routinator serves VRPs to Routers over RTR
    routinator=${ROUTINATOR_VERSION} \
    # Lightweight utilities (no heavy editors/development tools in prod)
    curl \
    # Process management
    procps \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Configure pip for system packages (required for Mininet)
RUN pip3 config set global.break-system-packages true

# Install FRR from official stable repository
RUN curl -s https://deb.frrouting.org/frr/keys.gpg | tee /usr/share/keyrings/frrouting.gpg > /dev/null && \
    echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr $(lsb_release -s -c) frr-stable" \
    | tee /etc/apt/sources.list.d/frr.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    frr \
    frr-pythontools \
    # bgpd's RPKI module (-M rpki). It depends on the exact frr version, so
    # it is installed with frr rather than pinned on its own.
    frr-rpki-rtrlib \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Node.js LTS (node, npm, npx) from the official NodeSource repository
ARG NODE_MAJOR=24
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Docker Engine (CLI + daemon) so ContainerHost nodes can run docker-in-docker
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(lsb_release -s -c) stable" \
      > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
      docker-ce \
      docker-ce-cli \
      containerd.io \
      docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# RPKI CA Service: krill and krillc (see the krill stages), with Krill's
# MPL-2.0 text and the notices of the crates and the runtime linked into it.
ARG TARGETARCH
RUN --mount=type=bind,from=krill,source=/usr/local/bin,target=/mnt/krill \
    case "$TARGETARCH" in \
      amd64) apt-get update \
        && apt-get install -y --no-install-recommends krill=${KRILL_PACKAGE_VERSION} \
        && rm -rf /var/lib/apt/lists/* ;; \
      arm64) install -m 0755 /mnt/krill/krill /mnt/krill/krillc /usr/local/bin/ ;; \
    esac
COPY licenses/krill/ /usr/share/doc/krill/

# Add root user to FRR groups for management access
RUN usermod -a -G frr,frrvty root

# Copy pre-built Mininet from builder stage (avoids recompilation)
COPY --from=builder /opt/mininet /opt/mininet

# Install Mininet with production-optimized flags
# Uses non-interactive installation and cleans up source after
RUN cd /opt/mininet && \
    apt-get update && \
    if ! apt-cache show openvswitch-controller >/dev/null 2>&1; then \
      apt-get install -y --no-install-recommends openvswitch-testcontroller || true; \
    fi && \
    PYTHON=python3 ./util/install.sh -nv && \
    # Clean up build artifacts to reduce image size
    rm -rf /opt/mininet/.git \
           /opt/mininet/examples \
           /opt/mininet/doc \
           /var/lib/apt/lists/* \
           /tmp/* \
           /var/tmp/*

# Remove build-time policy to allow services to start normally at runtime
RUN rm -f /usr/sbin/policy-rc.d || true

# Create application-specific directories
RUN mkdir -p /mininet-iplab \
    && chmod 755 /mininet-iplab

# Install mnIPLab dependencies. The dev lock (pytest, pylint, mypy) is
# baked in beside the runtime pins so the test suite runs in this image as
# built; CI relies on that rather than installing it per run.
COPY requirements.txt requirements-dev.lock /tmp/
RUN pip3 install --no-cache-dir \
      -r /tmp/requirements.txt -r /tmp/requirements-dev.lock && \
    rm /tmp/requirements.txt /tmp/requirements-dev.lock

# Copy and set up entrypoint (keeping your working version)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER root

WORKDIR /mininet-iplab

# Health check to verify service readiness
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ovs-vsctl show > /dev/null 2>&1 || exit 1

# Expose web UI port of Mininet-IPLab (default FastAPI port)
EXPOSE 8050

# Service start script (handles initialization)
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
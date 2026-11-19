# Multi-stage build for optimized image size

# Krill as the repository-root Dockerfile installs it: the Debian package on
# amd64, the static binaries from the official image on arm64. Keep these
# values and KRILL_PACKAGE_VERSION identical to the ones there.
ARG KRILL_VERSION=v0.16.1
ARG KRILL_DIGEST=sha256:cb80c99b68709cf7579e86250b4566b3a2513df995394e8a81ce89f65b8d2a32
FROM nlnetlabs/krill:${KRILL_VERSION}@${KRILL_DIGEST} AS krill-arm64
FROM mcr.microsoft.com/devcontainers/base:bookworm AS krill-amd64
FROM krill-${TARGETARCH} AS krill
FROM mcr.microsoft.com/devcontainers/base:bookworm AS builder

# The same pinned Mininet the production image and CI use. Keep this value
# identical to the ARG in the repository-root Dockerfile; CI fails if the
# two drift apart. This used to default to `master`, so a dev container built
# on a different day could hold a different Mininet than the image the tests
# are judged against.
ARG MININET_VERSION=6eb8973c0bfd13c25c244a3871130c5e36b5fbd7
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    ca-certificates \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Fetch the pinned Mininet. `git clone --branch` accepts only a branch or a
# tag, never a commit SHA; this spelling takes a SHA, a tag or a branch alike.
RUN git init -q /opt/mininet \
    && git -C /opt/mininet remote add origin https://github.com/mininet/mininet.git \
    && git -C /opt/mininet fetch -q --depth 1 origin ${MININET_VERSION} \
    && git -C /opt/mininet checkout -q FETCH_HEAD

# Production stage
FROM mcr.microsoft.com/devcontainers/base:bookworm

ARG KEA_VERSION=3.2.1-isc20260928103142
ARG ROUTINATOR_VERSION=0.16.1-1bookworm
ARG KRILL_PACKAGE_VERSION=0.16.1-1bookworm
ENV DEBIAN_FRONTEND=noninteractive

# Prevent services from being started during image build (temporary)
RUN printf '%s\n' '#!/bin/sh' 'exit 101' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d

# Metadata labels
LABEL org.opencontainers.image.title="Mininet DevContainer" \
      org.opencontainers.image.description="Mininet DevContainer with Linux Debian bookworm" \
      org.opencontainers.image.source="https://github.com/mininet/mininet" \
      org.opencontainers.image.licenses="MIT"

# Configure ISC's pinned Kea 3.2 repository.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
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
      > /etc/apt/sources.list.d/nlnetlabs.list

# Install runtime dependencies in optimized layers with BuildKit cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    # Core system packages
    python3 \
    python3-pip \
    python-is-python3 \
    python3-termcolor \
    python3-setuptools \
    python3-mako \
    patch \
    sudo \
    lsb-release \
    ca-certificates \
    # Networking packages
    openvswitch-switch \
    iproute2 \
    iputils-ping \
    net-tools \
    bridge-utils \
    tcpdump \
    traceroute \
    iptables \
    iptables-persistent \
    iperf3 \
    iperf \
    socat \
    telnet \
    netcat-traditional \
    curl \
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
    iputils-tracepath \
    tshark \
    # NAT Table: conntrack (conntrack-tools) lists a translating Router's
    # connection tracking table
    conntrack \
    # Development tools
    vim \
    bc \
    git \
    # Process management
    procps \
    && rm -rf /tmp/* /var/tmp/*

# Install FRR from official repository
RUN curl -s https://deb.frrouting.org/frr/keys.gpg | sudo tee /usr/share/keyrings/frrouting.gpg > /dev/null

ENV FRRVER="frr-stable"
ENV PATH="/usr/lib/frr:${PATH}"
RUN echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr $(lsb_release -s -c) $FRRVER" \
    | tee /etc/apt/sources.list.d/frr.list
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y frr frr-pythontools frr-rpki-rtrlib \
    && rm -rf /tmp/* /var/tmp/*

# Install Node.js LTS (node, npm, npx) from the official NodeSource repository
ARG NODE_MAJOR=24
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /tmp/* /var/tmp/*

# RPKI CA Service: krill and krillc, with their notices (see the krill stages)
ARG TARGETARCH
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=bind,from=krill,source=/usr/local/bin,target=/mnt/krill \
    case "$TARGETARCH" in \
      amd64) apt-get update \
        && apt-get install -y --no-install-recommends krill=${KRILL_PACKAGE_VERSION} ;; \
      arm64) install -m 0755 /mnt/krill/krill /mnt/krill/krillc /usr/local/bin/ ;; \
    esac
COPY licenses/krill/ /usr/share/doc/krill/

# Add root user to FRR groups for management access
RUN usermod -a -G frr,frrvty root

# Copy Mininet source from builder stage
COPY --from=builder /opt/mininet /opt/mininet

# Install Mininet with your working configuration
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    cd /opt/mininet && \
    apt-get update && \
    # Configure pip to allow system packages (your working fix)
    pip3 config set global.break-system-packages true && \
    # If openvswitch-controller package is missing on this platform, install the testcontroller as a safe fallback
    if ! apt-cache show openvswitch-controller >/dev/null 2>&1; then apt-get install -y --no-install-recommends openvswitch-testcontroller || true; fi && \
    # Install with your working flags
    PYTHON=python3 ./util/install.sh -nv && \
    # Clean up
    rm -rf /opt/mininet/.git /opt/mininet/examples /opt/mininet/doc

# Remove build-time policy to allow services to start normally at runtime
RUN rm -f /usr/sbin/policy-rc.d || true

# Install Mininet-IPLab dependencies and the dev tooling, so every test
# tier runs in the container as built with no postCreateCommand step.
COPY requirements.txt requirements-dev.lock /tmp/
RUN pip3 install -r /tmp/requirements.txt -r /tmp/requirements-dev.lock \
    && rm /tmp/requirements.txt /tmp/requirements-dev.lock

# Playwright's Chromium for the browser tier. It lives outside /root because
# devcontainer.json mounts a volume over /root, which would hide a copy
# baked in under the default ~/.cache/ms-playwright.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    playwright install --with-deps chromium

USER root
WORKDIR /workspaces/mininet-iplab

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ovs-vsctl show > /dev/null 2>&1 || exit 1

# Start OVS on container startup for devcontainer
CMD bash -c "service openvswitch-switch start && sleep infinity"

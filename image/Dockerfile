# Fleet (gVisor runtime) image for the cua-driver FPS movement benchmark.
#
# Base: the image cua-sandbox uses for local docker on trycua/cua main
# (cua-ubuntu-24.04:docker-latest). On top of it we add:
#   * bench_ui + pywebview/GTK so cua-bench's launch_window(html=...) works
#   * a Rust toolchain and a sparse clone of trycua/cua (libs/cua-driver) at a
#     pinned ref, with crates pre-fetched, so experiments can `git apply` a diff
#     and rebuild cua-driver inside the sandbox
#   * a supervisord program that builds cua-driver at container boot, so a warm
#     pool member already has a native release build when it is claimed
#
# Build for Fleet (amd64) with image/build.sh; PLATFORM=linux/arm64 PUSH=0 image/build.sh
# builds a native image for local docker testing on Apple Silicon.
FROM public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:docker-latest

ARG CUA_REF=dbf0d3a450d9c4fc3a0a768faf0ce9b3283f908e
ARG RUST_TOOLCHAIN=1.97.1
ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CUA_DRIVER_SRC=/opt/cua/libs/cua-driver

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config git ca-certificates curl \
        libx11-dev libxi-dev libxtst-dev libxext-dev libwayland-dev libxkbcommon-dev \
        python3-gi gir1.2-webkit2-4.1 gir1.2-gtk-3.0 python3-pip xdotool \
    && rm -rf /var/lib/apt/lists/*

# bench_ui is what cua-bench's launch_window() imports via the system python.
RUN python3 -m pip install --break-system-packages --no-cache-dir cua-bench-ui pywebview

RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path \
        --default-toolchain "${RUST_TOOLCHAIN}" --profile minimal \
    && rustc --version

RUN git clone --filter=blob:none --sparse https://github.com/trycua/cua.git /opt/cua \
    && cd /opt/cua && git sparse-checkout set libs/cua-driver \
    && git checkout --detach "${CUA_REF}"

# Overlay this repo's vendored cua-driver source (the tree the autoresearch loop
# patches, e.g. press_key hold_ms) on top of the upstream clone, then pre-fetch crates.
# Build context is the repo root (see image/build.sh).
COPY cua-driver/rust "${CUA_DRIVER_SRC}/rust"
RUN cd "${CUA_DRIVER_SRC}/rust" && rm -rf target && cargo fetch

COPY image/prebuild.sh /opt/fps-bench/prebuild.sh
COPY image/supervisor-prebuild.conf /etc/supervisor/conf.d/zz-cua-driver-prebuild.conf
# The base image's supervisord.conf has NO [include] section, so conf.d/ is never
# read (verified 2026-08-30: the prebuild program never spawned). Register the
# program in supervisord.conf itself, and add a control socket so
# `supervisorctl restart cua-driver-mcp` in prebuild.sh works after the build.
RUN chmod +x /opt/fps-bench/prebuild.sh \
    && printf '\n[unix_http_server]\nfile=/run/supervisor.sock\nchmod=0700\n\n[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n\n[supervisorctl]\nserverurl=unix:///run/supervisor.sock\n\n[include]\nfiles = /etc/supervisor/conf.d/*.conf\n' >> /etc/supervisor/supervisord.conf \
    && python3 -c "import configparser,sys; c=configparser.RawConfigParser(); c.read('/etc/supervisor/supervisord.conf'); c.read('/etc/supervisor/conf.d/zz-cua-driver-prebuild.conf'); assert 'program:cua-driver-prebuild' in c.sections() and 'include' in c.sections(); print('supervisor config ok')"

LABEL org.opencontainers.image.source="https://github.com/trycua/cua" \
      ai.cua.fps-bench.cua-ref="${CUA_REF}"

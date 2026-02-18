FROM ubuntu:22.04

RUN apt-get update && \
    apt-get install -y \
        build-essential curl git wget tar gdb vim \
        python3.11 python3-pip \
        pkg-config libssl-dev \
        protobuf-compiler libprotobuf-dev ninja-build \
        libseccomp-dev

# Install dependencies.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        lsb-release wget software-properties-common gnupg && \
    apt-get install -y wget libtool-bin automake flex bison \
        libglib2.0-dev libpixman-1-dev python3-setuptools unzip \
        apt-utils apt-transport-https ca-certificates joe curl && \
    wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && ./llvm.sh 17

# Common build dependencies
# bazel
RUN curl -LO "https://github.com/bazelbuild/bazelisk/releases/download/v1.27.0/bazelisk-linux-amd64" \
    && chmod +x bazelisk-linux-amd64 \
    && mv bazelisk-linux-amd64 /usr/local/bin/bazel

ENV TERM xterm-256color

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Make both the Rust cargo bin directory and our global gf tools directory
# available on PATH. rustup installs cargo into /root/.cargo/bin, while we
# install casr and stitch_libafl tooling under /opt/stitch_bin.
ENV PATH="/opt/stitch_bin/bin:/root/.cargo/bin:${PATH}"

# Install casr into a globally accessible prefix so any UID can execute it.
RUN cargo install casr --root /opt/stitch_bin

RUN curl -qsL 'https://install.pwndbg.re' | sh -s -- -t pwndbg-gdb

COPY /stitchi/requirements.txt /requirements.txt
RUN pip install -r requirements.txt

COPY rust-toolchain.toml /src/rust-toolchain.toml
COPY /stitch_core /src/stitch_core
COPY /stitch_libafl /src/stitch_libafl

ENV LIBAFL_EDGES_MAP_SIZE=800000

RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/src/stitch_libafl/target \
    --mount=type=cache,target=/src/stitch_core/target \
    --mount=type=cache,target=/root/.rustup \
    cd /src/stitch_libafl && \
    cargo build --release && \
    cargo install --path . --root /opt/stitch_bin && \
    mkdir -p /opt/stitch_bin/bin && \
    cp target/release/libstitch_libafl.a /opt/stitch_bin/bin/libstitch_libafl.a && \
    chmod -R a+rx /opt/stitch_bin && \
    mkdir -p /src/tmp && \
    cp -r target/release/build/libafl_cc-* /src/tmp && \
    cd /src/stitch_core && \
    cargo build --release && \
    cargo install --path . --root /opt/stitch_bin

RUN cd /src/stitch_libafl && \
    mkdir -p target/release/build && \
    cp -r /src/tmp/libafl_cc-* target/release/build/

COPY /stitchi /src/stitchi

RUN cd /src/stitchi && \
    pip install -e .

RUN groupadd -g 1000 fuzzer && \
    useradd -m -u 1000 -g fuzzer -s /bin/bash fuzzer && \
    chmod 0666 /etc/passwd

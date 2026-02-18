FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y \
        build-essential curl git wget tar gdb vim \
        python3.11 python3-pip \
        pkg-config libssl-dev \
        protobuf-compiler libprotobuf-dev ninja-build \
        libseccomp-dev \
        lsb-release software-properties-common gnupg \
        libtool-bin automake flex bison \
        libglib2.0-dev libpixman-1-dev python3-setuptools unzip \
        apt-utils apt-transport-https ca-certificates joe && \
    wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && ./llvm.sh 17

RUN curl -LO "https://github.com/bazelbuild/bazelisk/releases/download/v1.27.0/bazelisk-linux-amd64" \
    && chmod +x bazelisk-linux-amd64 \
    && mv bazelisk-linux-amd64 /usr/local/bin/bazel

ENV TERM xterm-256color

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

ENV PATH="/root/.cargo/bin:${PATH}"

# Install casr into a globally accessible prefix; the binaries will be placed
# under /usr/local/bin, which is already on PATH for all users.
RUN cargo install casr --root /usr/local

RUN groupadd -g 1000 fuzzer && \
    useradd -m -u 1000 -g fuzzer -s /bin/bash fuzzer && \
    chmod 0666 /etc/passwd

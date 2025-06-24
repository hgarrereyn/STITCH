FROM ubuntu:22.04
ENV TERM xterm-256color

RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get -y install \
    clang make cmake git wget curl tar gdb vim \
    python3.11 python3-pip \
    pkg-config libssl-dev \
    protobuf-compiler libprotobuf-dev ninja-build \
    universal-ctags

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

ENV PATH="/root/.cargo/bin:${PATH}"

RUN cargo install casr

COPY /agf/requirements.txt /requirements.txt
RUN pip install -r requirements.txt

COPY rust-toolchain.toml /src/rust-toolchain.toml
COPY /gf_core /src/gf_core
COPY /gf_libafl /src/gf_libafl

ENV LIBAFL_EDGES_MAP_SIZE=800000

RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/src/gf_libafl/target \
    --mount=type=cache,target=/src/gf_core/target \
    --mount=type=cache,target=/root/.rustup \
    cd /src/gf_libafl && \
    OUT_DIR=/deps cargo build --release && \
    cargo install --path . && \
    cp target/release/libgf_libafl.a /root/.cargo/bin/libgf_libafl.a && \
    mkdir -p /src/tmp && \
    cp -r target/release/build/libafl_cc-* /src/tmp && \
    cd /src/gf_core && \
    cargo build --release && \
    cargo install --path .

RUN cd /src/gf_libafl && \
    mkdir -p target/release/build && \
    cp -r /src/tmp/libafl_cc-* target/release/build/

COPY /agfi /src/agfi

RUN cd /src/agfi && \
    pip install -e .

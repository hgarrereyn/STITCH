FROM gfafl_base

RUN apt-get update && apt-get install -y cmake yasm wget
RUN git clone https://aomedia.googlesource.com/aom /fuzz/aom && \
    cd /fuzz/aom && \
    git checkout c606683

RUN cd /fuzz/aom && \
    mkdir -p aom_build && \
    cd aom_build && \
    cmake -G Ninja .. && \
    ninja -j$(nproc) && \
    ninja install

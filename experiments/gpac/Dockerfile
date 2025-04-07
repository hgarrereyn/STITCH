FROM gfafl_base

RUN apt-get update && apt-get install -y build-essential pkg-config libz-dev
RUN git clone https://github.com/gpac/gpac /fuzz/gpac && \
    cd /fuzz/gpac && \
    git checkout 9617407 && \
    git submodule update --init --remote --recursive

RUN cd /fuzz/gpac && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure && \
    make -j$(nproc) && \
    cp /fuzz/gpac/config.h /fuzz/gpac/include/gpac/configuration.h

COPY src /fuzz/src

WORKDIR /fuzz/src

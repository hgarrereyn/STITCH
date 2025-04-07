FROM gfafl_base

RUN apt-get update && apt-get install -y make cmake flex bison
RUN git clone https://github.com/the-tcpdump-group/libpcap.git /fuzz/libpcap && \
    cd /fuzz/libpcap && \
    git checkout bbcbc91

RUN cd /fuzz/libpcap && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j$(nproc) all && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

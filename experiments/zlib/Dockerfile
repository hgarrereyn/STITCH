FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool
RUN git clone https://github.com/madler/zlib.git /fuzz/zlib && \
    cd /fuzz/zlib && \
    git checkout 51b7f2a

RUN cd /fuzz/zlib && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure && \
    make -j$(nproc) all && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

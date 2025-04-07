FROM gfafl_base

RUN apt-get update && apt-get install -y libbz2-dev

RUN git clone https://github.com/ladislav-zezula/StormLib /fuzz/StormLib && \
    cd /fuzz/StormLib && \
    git checkout da046e7

RUN cd /fuzz/StormLib && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake . && \
    make -j$(nproc) && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src
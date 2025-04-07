FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool subversion sudo wget

RUN git clone https://github.com/stricaud/faup /fuzz/faup && \
    cd /fuzz/faup && \
    git checkout 28c08d0

RUN cd /fuzz/faup && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j$(nproc)

COPY src /fuzz/src

WORKDIR /fuzz/src
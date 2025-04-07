FROM gfafl_base

RUN git clone https://github.com/lexbor/lexbor.git /fuzz/lexbor && \
    cd /fuzz/lexbor && \
    git checkout 0c2bb06

RUN cd /fuzz/lexbor && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake . && \
    make -j$(nproc) && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

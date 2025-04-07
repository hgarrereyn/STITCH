FROM gfafl_base

RUN git clone https://github.com/libgeos/geos.git /fuzz/geos && \
    cd /fuzz/geos && \
    git checkout 1e4d0b0

RUN cd /fuzz/geos && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j$(nproc) all && \
    make install && \
    ldconfig

COPY src /fuzz/src

WORKDIR /fuzz/src

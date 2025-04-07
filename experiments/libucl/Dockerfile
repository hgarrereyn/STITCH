FROM gfafl_base

RUN apt-get update && apt-get install -y make cmake autoconf pkg-config libtool
RUN git clone https://github.com/vstakhov/libucl.git /fuzz/libucl && \
    cd /fuzz/libucl && \
    git checkout 62c862f

RUN cd /fuzz/libucl && \
    ./autogen.sh && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure && \
    make -j$(nproc) && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src
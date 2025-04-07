FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool
RUN git clone https://github.com/mm2/Little-CMS.git /fuzz/lcms && \
    cd /fuzz/lcms && \
    git checkout 5176347

RUN cd /fuzz/lcms && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure --enable-shared=no && \
    make -j$(nproc) all && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

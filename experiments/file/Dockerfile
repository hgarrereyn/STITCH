FROM gfafl_base

RUN apt-get update && apt install -y make autoconf automake libtool shtool
RUN apt-get update && apt install -y libbz2-dev      liblzma-dev      zlib1g-dev      libzstd-dev      liblz4-dev
RUN git clone https://github.com/file/file.git /fuzz/file && \
    cd /fuzz/file && \
    git checkout 4e847cd    

RUN cd /fuzz/file && \
    autoreconf -i && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure --enable-static --enable-fsect-man5 && \
    make -j$(nproc) && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

FROM gfafl_base

RUN apt-get update && apt-get install -y gcc autoconf automake libtool git make libltdl-dev pkg-config libyaml-dev

RUN git clone https://github.com/pantoniou/libfyaml /fuzz/libfyaml && \
    cd /fuzz/libfyaml && \
    git checkout 6e52e4d

RUN cd /fuzz/libfyaml && \
    ./bootstrap.sh && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure && \
    make -j$(nproc) && \
    make install && \
    ldconfig

COPY src /fuzz/src

WORKDIR /fuzz/src

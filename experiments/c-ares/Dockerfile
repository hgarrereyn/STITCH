FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool
RUN git clone https://github.com/c-ares/c-ares.git /fuzz/c-ares && \
    cd /fuzz/c-ares && \
    git checkout 7978cf7

RUN cd /fuzz/c-ares && \
    ./buildconf && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx ./configure --disable-shared && \
    make -j8 && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

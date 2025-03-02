
FROM gfafl_base

WORKDIR /fuzz
RUN git clone https://github.com/Softmotions/iowow.git

RUN cd /fuzz/iowow && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. -DCMAKE_BUILD_TYPE=Debug && \
    make && \
    make install

ENV LD_LIBRARY_PATH=/fuzz/iowow/build/src/

COPY src /fuzz/src

WORKDIR /fuzz/src

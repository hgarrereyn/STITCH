FROM gfafl_base

RUN apt-get update && apt-get install -y cmake

RUN git clone https://github.com/DaveGamble/cJSON.git /fuzz/cjson && \
    cd /fuzz/cjson && \
    git checkout 12c4bf1

RUN cd /fuzz/cjson && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j8 && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

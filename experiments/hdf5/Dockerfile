FROM gfafl_base

WORKDIR /fuzz
RUN git clone https://github.com/HDFGroup/hdf5.git && \
    cd hdf5 && \
    git checkout 7bf340440909d468dbb3cf41f0ea0d87f5050cea

RUN cd /fuzz/hdf5 && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j8 && \
    make install

ENV LD_LIBRARY_PATH=/fuzz/hdf5/build/bin/

COPY src /fuzz/src

WORKDIR /fuzz/src

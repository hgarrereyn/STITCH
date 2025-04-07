FROM gfafl_base

RUN apt-get update -y && apt-get install -y cmake make pkg-config

# https://github.com/abseil/abseil-cpp/issues/1524#issuecomment-1739364093
# explains why Abseil must be built for fuzzing, not depended on normally.
RUN git clone --depth=1 https://github.com/abseil/abseil-cpp /fuzz/abseil-cpp
RUN git clone https://github.com/google/re2 /fuzz/re2 && \
    cd /fuzz/re2 && \
    git checkout 6dcd83d

RUN cd /fuzz/abseil-cpp && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake -DCMAKE_POSITION_INDEPENDENT_CODE=ON .. && \
    make -j$(nproc) all && \
    make install && \
    ldconfig

RUN cd /fuzz/re2 && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake .. && \
    make -j$(nproc) all && \
    make install && \
    ldconfig

COPY src /fuzz/src

WORKDIR /fuzz/src

FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool zlib1g-dev
RUN git clone https://github.com/AcademySoftwareFoundation/openexr /fuzz/openexr && \
    cd /fuzz/openexr && \
    git checkout da760aa

RUN cd /fuzz/openexr && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake \
        -DBUILD_SHARED_LIBS=OFF \
        -DBUILD_TESTING=OFF \
        -DOPENEXR_INSTALL_EXAMPLES=OFF \
        -DOPENEXR_LIB_SUFFIX= \
        . && \
    make -j$(nproc)

COPY src /fuzz/src

WORKDIR /fuzz/src

FROM gfafl_base

RUN git clone https://github.com/libical/libical.git /fuzz/libical && \
    cd /fuzz/libical && \
    git checkout 50d1b5d

RUN cd /fuzz/libical && \
    mkdir build && \
    cd build && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake -DSTATIC_ONLY=ON -DICAL_GLIB=False -DICAL_GLIB_BUILD_DOCS=False -DGOBJECT_INTROSPECTION=False .. && \
    make -j$(nproc) all && \
    make install && \
    ldconfig

COPY src /fuzz/src

WORKDIR /fuzz/src

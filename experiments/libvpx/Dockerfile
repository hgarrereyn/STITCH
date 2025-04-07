FROM gfafl_base

RUN apt-get update && apt-get install -y yasm wget gcc
RUN git clone https://chromium.googlesource.com/webm/libvpx /fuzz/libvpx && \
    cd /fuzz/libvpx && \
    git checkout 7354f18

RUN cd /fuzz/libvpx && \
    ./configure \
        --enable-vp9-highbitdepth \
        --disable-unit-tests \
        --disable-examples \
        --size-limit=12288x12288 \
        --extra-cflags="-DVPX_MAX_ALLOCABLE_MEMORY=536870912" \
        --disable-webm-io \
        --enable-debug \
        --disable-vp8-encoder \
        --disable-vp9-encoder && \
    make -j$(nproc) all && \
    make install

COPY src /fuzz/src

WORKDIR /fuzz/src

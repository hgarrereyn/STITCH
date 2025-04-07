FROM gfafl_base

RUN apt-get update && \
    apt-get install -y make autoconf automake libtool zlib1g-dev

RUN git clone https://github.com/madler/zlib.git /fuzz/zlib && \
    cd /fuzz/zlib && \
    git checkout 5a82f71

RUN git clone https://github.com/pnggroup/libpng.git /fuzz/libpng && \
    cd /fuzz/libpng && \
    git checkout 34005e3

COPY build.sh /fuzz/libpng/build.sh
RUN cd /fuzz/libpng && ./build.sh

COPY src /fuzz/src

WORKDIR /fuzz/src

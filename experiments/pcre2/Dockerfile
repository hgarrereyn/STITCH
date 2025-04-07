FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool subversion

RUN git clone --recursive https://github.com/PCRE2Project/pcre2 /fuzz/pcre2 && \
    cd /fuzz/pcre2 && \
    git checkout c98e5d5

RUN cd /fuzz/pcre2 && \
    ./autogen.sh && \
    ./configure --enable-fuzz-support \
        --enable-never-backslash-C --with-match-limit=1000 --with-match-limit-depth=1000 \
        --enable-jit \
        --enable-pcre2-16 --enable-pcre2-32 && \
    make -j$(nproc) clean && \
    make -j$(nproc) all

COPY src /fuzz/src

WORKDIR /fuzz/src

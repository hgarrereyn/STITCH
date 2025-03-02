FROM gfafl_base

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    gcc \
    make \
    tcl-dev

WORKDIR /fuzz

RUN git clone https://github.com/sqlite/sqlite.git && \
    cd sqlite && \
    git checkout dadd0b4

RUN cd /fuzz && \
    mkdir build && \
    cd build && \
    ../sqlite/configure && \
    make sqlite3.c && \
    gf_libafl_cc -c -o sqlite3.o sqlite3.c

COPY src /fuzz/src

WORKDIR /fuzz/src


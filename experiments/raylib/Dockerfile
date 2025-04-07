FROM gfafl_base

RUN apt-get update && apt-get install -y make autoconf automake libtool subversion sudo wget

# Install newer cmake
RUN sudo apt remove --purge --auto-remove -y cmake && \
    wget https://github.com/Kitware/CMake/releases/download/v3.25.0/cmake-3.25.0-linux-x86_64.sh && \
    chmod +x cmake-3.25.0-linux-x86_64.sh && \
    ./cmake-3.25.0-linux-x86_64.sh --skip-license --prefix=/usr/local && \
    rm cmake-3.25.0-linux-x86_64.sh

RUN git clone https://github.com/raysan5/raylib /fuzz/raylib && \
    cd /fuzz/raylib && \
    git checkout 5aa3f0c

RUN cd /fuzz/raylib && \
    CC=gf_libafl_cc CXX=gf_libafl_cxx cmake . && \
    make PLATFORM=PLATFORM_DESKTOP RAYLIB_LIBTYPE=SHARED all -j$(nproc)

COPY src /fuzz/src

WORKDIR /fuzz/src

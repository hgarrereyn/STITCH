FROM gfafl_base

RUN git clone https://github.com/jkuhlmann/cgltf.git /fuzz/cgltf && \
    cd /fuzz/cgltf && \
    git checkout 08470a2

COPY src /fuzz/src

WORKDIR /fuzz/src
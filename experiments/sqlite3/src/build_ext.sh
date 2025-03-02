#!/bin/bash

# Usage: ./build.sh <name>
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <name>"
    exit 1
fi

gf_libafl_cxx -g -std=c++17 -o $1 $1.cpp /fuzz/build/sqlite3.o -I/fuzz/build/ -I/src/gf_synth/external/

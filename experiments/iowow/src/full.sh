#!/bin/bash

# Usage: ./build.sh <name>
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <name>"
    exit 1
fi

gf_synth $1.json --config config.json -o $1.cpp -s $1.schema
gf_libafl_cxx -g -std=c++17 --libafl -o $1 $1.cpp -liowow

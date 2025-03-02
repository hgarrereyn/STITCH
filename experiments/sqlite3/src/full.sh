#!/bin/bash

gf_synth $1.json --config config.json -o $1.cpp -s $1.schema
gf_libafl_cxx -g -std=c++17 --libafl -o $1 $1.cpp /fuzz/build/sqlite3.o -I/fuzz/build/

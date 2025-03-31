#!/bin/bash
gf_libafl_cxx -g -std=c++17 -o $1 $1.cpp lib.cpp -I/fuzz/src/ -I/src/gf_synth/external/
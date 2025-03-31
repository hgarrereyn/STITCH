#!/bin/bash
gf_libafl_cxx -g -std=c++17 --libafl -o $1 $1.cpp lib.cpp -I/fuzz/src/

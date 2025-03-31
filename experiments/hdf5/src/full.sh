#!/bin/bash
gf_synth $1.json --config config.json -o $1.cpp -s $1.schema
gf_libafl_cxx -g -std=c++17 --libafl -o $1 $1.cpp -L/fuzz/hdf5/build/bin/ -lhdf5 -I/fuzz/hdf5/src/ -I/fuzz/hdf5/src/H5FDsubfiling/ -I/fuzz/hdf5/build/src/

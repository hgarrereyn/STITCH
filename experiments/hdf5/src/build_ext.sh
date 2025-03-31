#!/bin/bash
gf_libafl_cxx -g -std=c++17 -o $1 $1.cpp -L/fuzz/hdf5/build/bin/ -lhdf5 -I/fuzz/hdf5/src/ -I/fuzz/hdf5/src/H5FDsubfiling/ -I/fuzz/hdf5/build/src/ -I/src/gf_synth/external/

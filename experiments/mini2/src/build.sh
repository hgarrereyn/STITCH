gf_synth fuzz.json -o fuzz.cpp -s fuzz.schema

gf_libafl_cxx -std=c++17 -o fuzz fuzz.cpp

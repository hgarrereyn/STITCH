gf_synth fuzz2.json -o fuzz2.cpp -s fuzz2.schema

gf_libafl_cxx --libafl -std=c++17 -o fuzz2 fuzz2.cpp

#!/bin/bash

# generate initial spec
echo "Generating initial spec..."
agf gen new -i "iowow/iwkv.h" -o iwkv.h.json /fuzz/iowow/src/kv/iwkv.h


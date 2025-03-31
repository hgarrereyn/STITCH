#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>

extern "C" {
    #include "hdf5.h"
}

int main() {
    hid_t loc_id = 2459565876494606882;
    char name[] = {'"', '"', '"', '"', 0};

    herr_t status = H5Gget_objinfo(loc_id, name, 2, nullptr);

    if (status < 0) {
        return -1;
    }

    return 0;
}
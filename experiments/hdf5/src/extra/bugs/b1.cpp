#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>

extern "C" {
    #include "hdf5.h"
}

int main() {
    auto callback = [](hid_t id, void* udata) -> herr_t {
        // pass
        return 0;
    };

    H5I_type_t type = H5I_type_t::H5I_VOL;
    herr_t ret_value = H5Iiterate(type, callback, nullptr);

    return 0;
}

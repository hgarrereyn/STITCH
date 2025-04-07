#include <cstdio>
#include <cstdlib>
#include <hdf5.h>

int main() {
    hid_t file = H5Fcreate("test_file.h5", H5F_ACC_TRUNC, H5P_DEFAULT, H5P_DEFAULT);

    size_t size_hint = 0x40f1f1f2;
    hid_t group = H5Gcreate1(file, "my_group", size_hint);

    return 0;
}
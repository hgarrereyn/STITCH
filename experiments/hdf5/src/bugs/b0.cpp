#include <cstdio>
#include <cstdlib>
#include <hdf5.h>

int main() {
    hid_t file = H5Fcreate("test_file.h5", H5F_ACC_TRUNC, H5P_DEFAULT, H5P_DEFAULT);
    hid_t var_0 = H5Gcreate(file, "my_group", H5P_DEFAULT, H5P_DEFAULT, H5P_DEFAULT);

    char *name = (char*)malloc(0);
    ssize_t ret = H5Iget_name(var_0, name, 0);

    free(name);
    return 0;
}
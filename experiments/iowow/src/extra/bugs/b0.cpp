#include <cstdio>
#include <cstdlib>
#include "iowow/iwkv.h"
#include "gf_util.h"

int main() {
    struct iwkv_opts var_0;
    var_0.path = gf_fuzz_param::write_to_temp_file((char[32]){0}, 32);
    var_0.random_seed = 0;
    var_0.fmt_version = 0;
    var_0.oflags = 0;
    var_0.file_lock_fail_fast = true;
    var_0.wal.enabled = true;
    var_0.wal.check_crc_on_checkpoint = false;
    var_0.wal.savepoint_timeout_sec = 10;
    var_0.wal.checkpoint_timeout_sec = 300;
    var_0.wal.wal_buffer_sz = 8192;
    var_0.wal.checkpoint_buffer_sz = 1073741824;

    IWKV var_2;
    iwrc result = iwkv_open(&var_0, &var_2);

    return result != 0 ? 1 : 0;
}


#include <cstdint>

typedef int32_t handle_t;

handle_t create_handle();

void destroy_handle(handle_t handle);

void use_handle(handle_t handle);

void open_file(handle_t handle, const char *filename);

void close_file(handle_t handle);

void set_handle_name(handle_t handle, const char *name);

void get_handle_name(handle_t handle, char *name);

#include <assert.h>

int main(void) {
    /* This assertion deliberately fails and carries a custom message */
    assert(0 && "custom assertion message: expected non-zero");
    return 0; /* Unreachable */
}

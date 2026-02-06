#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char *buf = (char *)malloc(8);
    buf[0] = 'A';

    free(buf);

    buf[0] = 'B';

    return 0;
}

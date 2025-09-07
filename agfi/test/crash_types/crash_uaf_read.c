#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char *buf = (char *)malloc(8);
    buf[0] = 'A';

    free(buf);

    char c = buf[0];

    return 0;
}

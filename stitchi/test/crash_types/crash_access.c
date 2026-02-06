#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char *buf = (char *)0x1;
    buf[0] = 'A';

    return 0;
}

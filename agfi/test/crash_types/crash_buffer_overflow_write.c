#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char *buf = (char *)malloc(8);
    buf[8] = 'A';
    return 0;
}

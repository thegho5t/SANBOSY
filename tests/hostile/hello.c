#include <stdio.h>
#include <math.h>

int main(void) {
    char buf[64];
    if (fgets(buf, sizeof buf, stdin)) {
        for (char *p = buf; *p; ++p)
            if (*p >= 'a' && *p <= 'z') *p -= 32;
        printf("hello from C\n%s", buf);
    }
    printf("sqrt(2)=%.4f\n", sqrt(2.0));
    return 0;
}

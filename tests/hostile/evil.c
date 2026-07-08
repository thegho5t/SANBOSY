/* Compiled hostile code: try to escape at RUN time (post-compile). */
#include <stdio.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

int main(void) {
    /* fs escape */
    FILE *f = fopen("/etc/passwd", "w");
    printf("write /etc/passwd: %s\n", f ? "OPEN -- FAIL" : "blocked");
    if (f) fclose(f);

    /* network */
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_port = htons(80);
    inet_pton(AF_INET, "1.1.1.1", &a.sin_addr);
    int rc = (s >= 0) ? connect(s, (struct sockaddr *)&a, sizeof a) : -1;
    printf("network connect: %s\n", rc == 0 ? "OPEN -- FAIL" : "blocked");
    return 0;
}

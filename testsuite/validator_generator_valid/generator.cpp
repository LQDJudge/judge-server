#include <cstdio>
#include <cstdlib>

int main(int argc, char *argv[]) {
    int n = 0;
    if (argc > 1) {
        n = atoi(argv[1]);
    }
    printf("%d\n", n);
    fprintf(stderr, "%d\n", n);
    return 0;
}

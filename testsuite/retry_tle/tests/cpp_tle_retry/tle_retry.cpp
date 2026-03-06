// Test for TLE retry input reset fix.
// Uses clock() to consume exactly 1.2s of CPU time then exit.
// With time_limit=1.0, this always TLEs with execution_time ~1.0-1.2s,
// which is within the 0.5s retry threshold (< 1.5s).
//
// With the seek(0) fix: every retry re-reads input, runs 1.2s, TLEs.
// Without the fix: retry gets EOF (fd consumed), scanf fails, program
// exits immediately with no output, producing WA instead of TLE.
#include <cstdio>
#include <ctime>

int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    // Busy-wait for 1.2s of CPU time (> 1.0s limit, < 1.5s retry threshold)
    clock_t start = clock();
    while ((double)(clock() - start) / CLOCKS_PER_SEC < 1.2);
    printf("%d\n", n);
    return 0;
}

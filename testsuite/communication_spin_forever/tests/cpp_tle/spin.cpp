#include <iostream>

using namespace std;

static volatile int sink = 0;

int main() {
    string payload;
    int index;
    cin >> payload >> index;
    for (;;) {
        sink++;
    }
}

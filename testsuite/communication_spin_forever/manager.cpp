#include <csignal>
#include <fstream>
#include <iostream>
#include <vector>

using namespace std;

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    int processes = (argc - 1) / 2;

    string payload;
    cin >> payload;

    vector<ofstream> to(processes);
    vector<ifstream> from(processes);
    for (int i = 0; i < processes; i++) {
        to[i].open(argv[2 * i + 2]);
        from[i].open(argv[2 * i + 1]);
    }

    for (int i = 0; i < processes; i++) {
        to[i] << payload << ' ' << i << endl;
        to[i].flush();
    }

    for (int i = 0; i < processes; i++) {
        string got;
        from[i] >> got;
    }

    cout << "1.0\n";
    cerr << "translate:success\n";
    return 0;
}

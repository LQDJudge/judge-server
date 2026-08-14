#include <chrono>
#include <csignal>
#include <fstream>
#include <iostream>
#include <thread>
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

    this_thread::sleep_for(chrono::seconds(2));

    for (int i = 0; i < processes; i++) {
        to[i] << payload << ' ' << i << endl;
        to[i].close();
    }

    for (int i = 0; i < processes; i++) {
        string got;
        from[i] >> got;
        if (got != payload + "_" + to_string(i)) {
            cout << "0.0\n";
            cerr << "translate:wrong\n";
            return 0;
        }
    }

    cout << "1.0\n";
    cerr << "translate:success\n";
    return 0;
}

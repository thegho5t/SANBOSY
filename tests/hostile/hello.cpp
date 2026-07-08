#include <iostream>
#include <string>
#include <algorithm>
#include <vector>

int main() {
    std::string line;
    std::getline(std::cin, line);
    std::transform(line.begin(), line.end(), line.begin(), ::toupper);
    std::vector<int> v{3, 1, 2};
    std::sort(v.begin(), v.end());
    std::cout << "hello from C++\n" << line << "\nsorted:";
    for (int x : v) std::cout << ' ' << x;
    std::cout << '\n';
}

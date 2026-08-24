#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    // Package identity anchor only. Explorer invokes the COM server, not this EXE.
    return 0;
}

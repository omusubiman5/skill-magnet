#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    // Sparse packages require an application executable as an identity anchor.
    // Explorer tasks are launched by SkillMagnetCommand.dll directly; this
    // self-signed binary is deliberately never used as a process adapter.
    return ERROR_NOT_SUPPORTED;
}

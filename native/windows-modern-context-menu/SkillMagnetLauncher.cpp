#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <vector>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR command_line, int) {
    // This GUI-subsystem process is both the package identity anchor and the
    // classic-menu adapter. It launches only the command embedded in Skill
    // Magnet's installed menu contract; no shell is involved.
    if (!command_line || !*command_line) return ERROR_INVALID_PARAMETER;
    std::vector<wchar_t> mutable_command(
        command_line, command_line + wcslen(command_line));
    mutable_command.push_back(L'\0');
    STARTUPINFOW startup{sizeof(startup)};
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE,
                        CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, nullptr,
                        nullptr, &startup, &process)) {
        return static_cast<int>(GetLastError());
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}

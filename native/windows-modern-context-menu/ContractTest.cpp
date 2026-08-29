#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shobjidl.h>

#include <iostream>
#include <string>
#include <vector>

using GetClassObject = HRESULT(__stdcall*)(REFCLSID, REFIID, void**);
using CanUnload = HRESULT(__stdcall*)();

static const CLSID CLSID_SkillMagnetCommand = {
    0x13e2a9dd, 0x4378, 0x4f9d, {0xa3, 0x85, 0x97, 0x3c, 0x61, 0xb1, 0x9e, 0x63}};

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) return 2;
    wchar_t temporary_root[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, temporary_root)) return 8;
    const std::wstring local_app_data =
        std::wstring(temporary_root) + L"SkillMagnetContract-" +
        std::to_wstring(GetCurrentProcessId());
    if (!CreateDirectoryW(local_app_data.c_str(), nullptr) &&
        GetLastError() != ERROR_ALREADY_EXISTS) return 9;
    if (!SetEnvironmentVariableW(L"LOCALAPPDATA", local_app_data.c_str())) return 10;
    const std::wstring invoke_log =
        local_app_data + L"\\SkillMagnet\\ContextMenu\\invoke.log";
    HMODULE module = LoadLibraryW(argv[1]);
    if (!module) return 3;
    auto get_class = reinterpret_cast<GetClassObject>(GetProcAddress(module, "DllGetClassObject"));
    auto can_unload = reinterpret_cast<CanUnload>(GetProcAddress(module, "DllCanUnloadNow"));
    if (!get_class || !can_unload) return 4;

    IClassFactory* factory = nullptr;
    if (FAILED(get_class(CLSID_SkillMagnetCommand, IID_IClassFactory,
                         reinterpret_cast<void**>(&factory)))) return 5;
    IExplorerCommand* command = nullptr;
    if (FAILED(factory->CreateInstance(nullptr, __uuidof(IExplorerCommand),
                                       reinterpret_cast<void**>(&command)))) return 6;
    factory->Release();

    PWSTR title = nullptr;
    EXPCMDSTATE state{};
    EXPCMDFLAGS flags{};
    GUID canonical{};
    const bool valid = SUCCEEDED(command->GetTitle(nullptr, &title)) &&
        title && wcscmp(title, L"Skill Magnet") == 0 &&
        SUCCEEDED(command->GetState(nullptr, FALSE, &state)) && state == ECS_ENABLED &&
        SUCCEEDED(command->GetFlags(&flags)) && (flags & ECF_HASSUBCOMMANDS) != 0 &&
        SUCCEEDED(command->GetCanonicalName(&canonical)) &&
        canonical == CLSID_SkillMagnetCommand;
    CoTaskMemFree(title);

    auto child_titles = [](IExplorerCommand* parent) -> std::vector<std::wstring> {
        std::vector<std::wstring> titles;
        IEnumExplorerCommand* children = nullptr;
        if (FAILED(parent->EnumSubCommands(&children)) || !children) return titles;
        while (true) {
            IExplorerCommand* child = nullptr;
            ULONG fetched = 0;
            const HRESULT next = children->Next(1, &child, &fetched);
            if (next != S_OK || fetched != 1 || !child) break;
            PWSTR child_title = nullptr;
            if (SUCCEEDED(child->GetTitle(nullptr, &child_title)) && child_title) {
                titles.emplace_back(child_title);
            }
            CoTaskMemFree(child_title);
            child->Release();
        }
        children->Release();
        return titles;
    };
    const std::vector<std::wstring> expected_titles = {
        L"Test pack",
    };
    const bool hierarchy = child_titles(command) == expected_titles;
    const bool enumeration_is_silent =
        GetFileAttributesW(invoke_log.c_str()) == INVALID_FILE_ATTRIBUTES;
    command->Release();
    const bool unloadable = can_unload() == S_OK;
    FreeLibrary(module);
    RemoveDirectoryW(local_app_data.c_str());
    if (!valid || !hierarchy || !enumeration_is_silent || !unloadable) return 7;
    std::wcout << L"SkillMagnet IExplorerCommand contract PASS\n";
    return 0;
}

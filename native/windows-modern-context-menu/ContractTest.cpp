#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlobj_core.h>
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
    const HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(com)) return 11;
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

    // Invoke a real child with a filesystem *file* as the selected path. The
    // selected path is a --project argument, not a safe process working
    // directory. Passing it as lpCurrentDirectory reproduces Windows error 267.
    const std::wstring selected_file = local_app_data + L"\\selected-file.txt";
    HANDLE selected_handle = CreateFileW(
        selected_file.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (selected_handle == INVALID_HANDLE_VALUE) return 12;
    CloseHandle(selected_handle);
    IShellItem* selected_item = nullptr;
    IShellItemArray* selected_items = nullptr;
    IEnumExplorerCommand* invoke_children = nullptr;
    IExplorerCommand* invoke_child = nullptr;
    ULONG fetched = 0;
    bool invoke_succeeded = false;
    if (SUCCEEDED(SHCreateItemFromParsingName(
            selected_file.c_str(), nullptr, IID_PPV_ARGS(&selected_item))) &&
        SUCCEEDED(SHCreateShellItemArrayFromShellItem(
            selected_item, IID_PPV_ARGS(&selected_items))) &&
        SUCCEEDED(command->EnumSubCommands(&invoke_children)) && invoke_children &&
        invoke_children->Next(1, &invoke_child, &fetched) == S_OK &&
        fetched == 1 && invoke_child) {
        invoke_succeeded = SUCCEEDED(invoke_child->Invoke(selected_items, nullptr));
    }
    if (invoke_child) invoke_child->Release();
    if (invoke_children) invoke_children->Release();
    if (selected_items) selected_items->Release();
    if (selected_item) selected_item->Release();
    DeleteFileW(selected_file.c_str());
    command->Release();
    const bool unloadable = can_unload() == S_OK;
    FreeLibrary(module);
    RemoveDirectoryW(local_app_data.c_str());
    CoUninitialize();
    if (!valid || !hierarchy || !enumeration_is_silent || !invoke_succeeded || !unloadable) return 7;
    std::wcout << L"SkillMagnet IExplorerCommand contract PASS\n";
    return 0;
}

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <ocidl.h>
#include <servprov.h>
#include <shlobj_core.h>
#include <shobjidl.h>

#include <atomic>
#include <iostream>
#include <string>
#include <vector>

#include "RecoveryMessages.h"

using GetClassObject = HRESULT(__stdcall*)(REFCLSID, REFIID, void**);
using CanUnload = HRESULT(__stdcall*)();
using NativeSourceSha256 = const wchar_t*(__stdcall*)();

static const CLSID CLSID_SkillMagnetCommand = {
    0x13e2a9dd, 0x4378, 0x4f9d, {0xa3, 0x85, 0x97, 0x3c, 0x61, 0xb1, 0x9e, 0x63}};

class TestFolderViewSite final : public IServiceProvider, public IFolderView,
                                 public IPersistFolder2 {
public:
    explicit TestFolderViewSite(PCIDLIST_ABSOLUTE folder) : folder_(ILCloneFull(folder)) {}
    ~TestFolderViewSite() { ILFree(folder_); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (iid == IID_IUnknown || iid == IID_IServiceProvider) {
            *value = static_cast<IServiceProvider*>(this);
        } else if (iid == IID_IFolderView) {
            *value = static_cast<IFolderView*>(this);
        } else if (iid == IID_IPersist || iid == IID_IPersistFolder ||
                   iid == IID_IPersistFolder2) {
            *value = static_cast<IPersistFolder2*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG count = --references_;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE QueryService(REFGUID service, REFIID iid, void** value) override {
        if (service != SID_SFolderView) return E_NOINTERFACE;
        return QueryInterface(iid, value);
    }
    HRESULT STDMETHODCALLTYPE GetCurrentViewMode(UINT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetCurrentViewMode(UINT) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFolder(REFIID iid, void** value) override {
        return QueryInterface(iid, value);
    }
    HRESULT STDMETHODCALLTYPE Item(int, PITEMID_CHILD*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ItemCount(UINT, int*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Items(UINT, REFIID, void**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetSelectionMarkedItem(int*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFocusedItem(int*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetItemPosition(PCUITEMID_CHILD, POINT*) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetSpacing(POINT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDefaultSpacing(POINT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetAutoArrange() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SelectItem(int, DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SelectAndPositionItems(
        UINT, PCUITEMID_CHILD_ARRAY, POINT*, DWORD) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetClassID(CLSID*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Initialize(PCIDLIST_ABSOLUTE) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetCurFolder(PIDLIST_ABSOLUTE* folder) override {
        if (!folder) return E_POINTER;
        *folder = folder_ ? ILCloneFull(folder_) : nullptr;
        return *folder ? S_OK : E_OUTOFMEMORY;
    }

private:
    std::atomic<ULONG> references_{1};
    PIDLIST_ABSOLUTE folder_{};
};

static std::wstring ReadWideText(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return {};
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 ||
        size.QuadPart % static_cast<LONGLONG>(sizeof(wchar_t)) != 0) {
        CloseHandle(file);
        return {};
    }
    std::wstring text(static_cast<size_t>(size.QuadPart / sizeof(wchar_t)), L'\0');
    DWORD read = 0;
    const BOOL ok = ReadFile(file, text.data(), static_cast<DWORD>(size.QuadPart), &read, nullptr);
    CloseHandle(file);
    return ok && read == size.QuadPart ? text : std::wstring();
}

static std::string ReadBytes(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return {};
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 8 * 1024 * 1024) {
        CloseHandle(file);
        return {};
    }
    std::string bytes(static_cast<size_t>(size.QuadPart), '\0');
    DWORD read = 0;
    const BOOL ok = ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &read, nullptr);
    CloseHandle(file);
    return ok && read == bytes.size() ? bytes : std::string();
}

static bool WriteBytes(const std::wstring& path, const std::string& bytes) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const BOOL ok = WriteFile(file, bytes.data(), static_cast<DWORD>(bytes.size()),
                              &written, nullptr);
    CloseHandle(file);
    return ok && written == bytes.size();
}

static bool WriteWideText(const std::wstring& path, const std::wstring& text) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    const DWORD byte_count = static_cast<DWORD>(text.size() * sizeof(wchar_t));
    DWORD written = 0;
    const BOOL ok = WriteFile(file, text.data(), byte_count, &written, nullptr);
    CloseHandle(file);
    return ok && written == byte_count;
}

static bool IsLowerHexSha256(const std::wstring& value) {
    if (value.size() != 64) return false;
    for (const wchar_t character : value) {
        if (!((character >= L'0' && character <= L'9') ||
              (character >= L'a' && character <= L'f'))) {
            return false;
        }
    }
    return true;
}

static bool NativeSourceManifestMatches(const std::string& manifest,
                                        const std::wstring& digest) {
    if (!IsLowerHexSha256(digest)) return false;
    std::string digest_utf8;
    digest_utf8.reserve(digest.size());
    for (const wchar_t character : digest) {
        digest_utf8.push_back(static_cast<char>(character));
    }
    if (digest_utf8.size() != 64) return false;
    const std::string schema = "\"schema_version\":1";
    const std::string contract =
        "\"contract\":\"skill-magnet-native-source-v1\"";
    const std::string binding =
        "\"source_tree_sha256\":\"" + digest_utf8 + "\"";
    return manifest.find(schema) != std::string::npos &&
        manifest.find(contract) != std::string::npos &&
        manifest.find(binding) != std::string::npos;
}

static std::string WideToUtf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                        static_cast<int>(value.size()), nullptr, 0,
                                        nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), result.data(), size,
                            nullptr, nullptr) != size) {
        return {};
    }
    return result;
}

static std::wstring QuoteCommandArgument(const std::wstring& value) {
    std::wstring output = L"\"";
    size_t slashes = 0;
    for (const wchar_t character : value) {
        if (character == L'\\') {
            ++slashes;
        } else if (character == L'\"') {
            output.append(slashes * 2 + 1, L'\\');
            output.push_back(L'\"');
            slashes = 0;
        } else {
            output.append(slashes, L'\\');
            slashes = 0;
            output.push_back(character);
        }
    }
    output.append(slashes * 2, L'\\');
    output.push_back(L'\"');
    return output;
}

static std::string ProbeManifest(const std::wstring& executable,
                                 const std::wstring& output_path) {
    const std::wstring command = QuoteCommandArgument(executable) +
        L" --argv-probe " + QuoteCommandArgument(output_path) +
        L" __SKILL_MAGNET_PROJECT__";
    const std::wstring manifest =
        L"skill-magnet-menu-v4\r\n"
        L"__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
        L"Argument quoting contract probe.\t" + command + L"\r\n";
    return WideToUtf8(manifest);
}

static bool CreateCommandForManifest(const std::wstring& manifest_path,
                                     const std::string& manifest,
                                     IClassFactory* factory,
                                     IExplorerCommand** command) {
    if (!command) return false;
    *command = nullptr;
    return !manifest.empty() && WriteBytes(manifest_path, manifest) &&
        SUCCEEDED(factory->CreateInstance(nullptr, __uuidof(IExplorerCommand),
                                          reinterpret_cast<void**>(command))) &&
        *command != nullptr;
}

static bool Contains(const std::wstring& text, const wchar_t* token) {
    return token && text.find(token) != std::wstring::npos;
}

static bool RecoveryMessagesAreActionable() {
    static constexpr wchar_t config_path[] =
        L"C:\\Skill Magnet\\owner's config\\skill-magnet.json";
    const std::wstring independent_repair =
        SkillMagnetRecovery::InstalledPythonRepairCommand(config_path);
    const std::wstring missing = SkillMagnetRecovery::CreateProcessFailureMessage(
        ERROR_FILE_NOT_FOUND,
        L"Windows error 2: The system cannot find the file specified.",
        independent_repair);
    const std::wstring blocked = SkillMagnetRecovery::CreateProcessFailureMessage(
        ERROR_ACCESS_DENIED, L"Windows error 5: Access is denied.", independent_repair);
    const std::wstring invalid = SkillMagnetRecovery::CreateProcessFailureMessage(
        ERROR_BAD_EXE_FORMAT, L"Windows error 193: Bad executable format.",
        independent_repair);
    const std::wstring other = SkillMagnetRecovery::CreateProcessFailureMessage(
        ERROR_NOT_ENOUGH_MEMORY, L"Windows error 8: Not enough memory.",
        independent_repair);
    const std::wstring wait = SkillMagnetRecovery::WaitFailureMessage(
        L"Windows error 5: Access is denied.", independent_repair);
    const std::wstring immediate =
        SkillMagnetRecovery::ImmediateExitFailureMessage(7, independent_repair);
    static constexpr wchar_t old_submenu_instruction[] =
        L"Skill Magnet > " L"Library Manager";
    const auto actionable = [](const std::wstring& message) {
        return Contains(message, L"復旧手順:") && Contains(message, L"再実行") &&
            Contains(message, L"install-context-menu") && Contains(message, L"--config") &&
            Contains(message, L"修復") &&
            Contains(message, L"Ctrl+C") &&
            Contains(message, L"診断ログ:") &&
            Contains(message, L"%LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log") &&
            Contains(message, L"メモ帳") && Contains(message, L"event=");
    };
    return actionable(missing) && actionable(blocked) && actionable(invalid) &&
        actionable(other) && actionable(wait) && actionable(immediate) &&
        Contains(missing, L"Windows error 2:") &&
        Contains(missing, L"実行fileが存在しません") &&
        Contains(blocked, L"application-control policy") &&
        Contains(invalid, L"architecture") &&
        Contains(other, L"停止したSkill Magnet") &&
        Contains(wait, L"Windows error 5:") &&
        Contains(immediate, L"process終了コード: 7") &&
        Contains(independent_repair, L"$python = $null") &&
        Contains(independent_repair, L"Get-Command py.exe") &&
        Contains(independent_repair, L"Get-Command python.exe") &&
        Contains(independent_repair, L"sys.version_info >= (3, 12)") &&
        Contains(independent_repair, L"Python 3.12以降が導入されていません。") &&
        Contains(independent_repair,
                 L"--config 'C:\\Skill Magnet\\owner''s config\\skill-magnet.json'") &&
        !Contains(independent_repair, L"powershell.exe -NoProfile -Command") &&
        !Contains(independent_repair, L"exit ") &&
        Contains(missing, independent_repair.c_str()) &&
        !Contains(missing, L"C:\\Python312\\python.exe") &&
        Contains(wait, independent_repair.c_str()) &&
        Contains(immediate, independent_repair.c_str()) &&
        !Contains(missing, old_submenu_instruction) &&
        !Contains(wait, old_submenu_instruction) &&
        !Contains(immediate, old_submenu_instruction);
}

static bool ManifestCreatesDisabledRoot(
    const std::wstring& path, const std::string& manifest, IClassFactory* factory) {
    if (!WriteBytes(path, manifest)) return false;
    IExplorerCommand* command = nullptr;
    if (FAILED(factory->CreateInstance(nullptr, __uuidof(IExplorerCommand),
                                       reinterpret_cast<void**>(&command))) || !command) {
        return false;
    }
    EXPCMDSTATE state = ECS_ENABLED;
    EXPCMDFLAGS flags = ECF_HASSUBCOMMANDS;
    IEnumExplorerCommand* children = nullptr;
    const bool rejected =
        SUCCEEDED(command->GetState(nullptr, FALSE, &state)) && state == ECS_DISABLED &&
        SUCCEEDED(command->GetFlags(&flags)) && flags == ECF_DEFAULT &&
        command->EnumSubCommands(&children) == E_NOTIMPL && !children &&
        command->Invoke(nullptr, nullptr) == E_NOTIMPL;
    if (children) children->Release();
    command->Release();
    return rejected;
}

static bool FindSelectionDigest(const std::wstring& log, const wchar_t* source,
                                std::wstring* digest) {
    if (!digest) return false;
    digest->clear();
    const std::wstring source_field = L"selection_source=" + std::wstring(source);
    size_t position = 0;
    while ((position = log.find(source_field, position)) != std::wstring::npos) {
        const size_t line_start = log.rfind(L'\n', position);
        const size_t content_start = line_start == std::wstring::npos ? 0 : line_start + 1;
        const size_t line_end = log.find(L'\n', position);
        const std::wstring line = log.substr(
            content_start,
            (line_end == std::wstring::npos ? log.size() : line_end) - content_start);
        if (line.find(L"event=selection_succeeded") != std::wstring::npos) {
            const std::wstring field = L"project_sha256=";
            const size_t digest_start = line.find(field);
            if (digest_start != std::wstring::npos) {
                const size_t value_start = digest_start + field.size();
                const size_t value_end = line.find_first_of(L"\t\r\n", value_start);
                *digest = line.substr(
                    value_start,
                    (value_end == std::wstring::npos ? line.size() : value_end) - value_start);
                bool hexadecimal = digest->size() == 64;
                for (const wchar_t character : *digest) {
                    hexadecimal = hexadecimal &&
                        ((character >= L'0' && character <= L'9') ||
                         (character >= L'a' && character <= L'f'));
                }
                return hexadecimal;
            }
        }
        position += source_field.size();
    }
    return false;
}

static std::wstring ExpectedProjectDigest(const std::wstring& value) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_size = 0;
    DWORD hash_size = 0;
    DWORD copied = 0;
    std::wstring result;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(
            &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0)) ||
        !BCRYPT_SUCCESS(BCryptGetProperty(
            algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_size),
            sizeof(object_size), &copied, 0)) ||
        !BCRYPT_SUCCESS(BCryptGetProperty(
            algorithm, BCRYPT_HASH_LENGTH, reinterpret_cast<PUCHAR>(&hash_size),
            sizeof(hash_size), &copied, 0))) {
        if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
        return {};
    }
    std::vector<UCHAR> object(object_size);
    std::vector<UCHAR> digest(hash_size);
    PUCHAR bytes = reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(value.data()));
    const ULONG byte_count = static_cast<ULONG>(value.size() * sizeof(wchar_t));
    if (BCRYPT_SUCCESS(BCryptCreateHash(
            algorithm, &hash, object.data(), object_size, nullptr, 0, 0)) &&
        BCRYPT_SUCCESS(BCryptHashData(hash, bytes, byte_count, 0)) &&
        BCRYPT_SUCCESS(BCryptFinishHash(hash, digest.data(), hash_size, 0))) {
        static constexpr wchar_t hex[] = L"0123456789abcdef";
        result.reserve(digest.size() * 2);
        for (const UCHAR byte : digest) {
            result.push_back(hex[byte >> 4]);
            result.push_back(hex[byte & 0x0f]);
        }
    }
    if (hash) BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return result;
}

int wmain(int argc, wchar_t** argv) {
    if (argc >= 2 && wcscmp(argv[1], L"--argv-probe") == 0) {
        if (argc != 4 || !argv[2] || !argv[3] || !*argv[3]) return 40;
        return WriteWideText(argv[2], argv[3]) ? 0 : 41;
    }
    if (argc != 2) return 2;
    const HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(com)) return 11;
    SetEnvironmentVariableW(L"SKILL_MAGNET_NATIVE_CONTRACT_TEST", L"1");
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
    const std::wstring dll_path = argv[1];
    const size_t dll_slash = dll_path.find_last_of(L"\\/");
    if (dll_slash == std::wstring::npos) return 15;
    const std::wstring manifest_path =
        dll_path.substr(0, dll_slash + 1) + L"SkillMagnetMenu.tsv";
    const std::wstring source_manifest_path =
        dll_path.substr(0, dll_slash + 1) + L"SkillMagnetNativeSource.json";
    const std::string original_manifest = ReadBytes(manifest_path);
    if (original_manifest.empty()) return 16;
    const std::string source_manifest = ReadBytes(source_manifest_path);
    if (source_manifest.empty()) return 19;

    HMODULE module = LoadLibraryW(argv[1]);
    if (!module) return 3;
    auto get_class = reinterpret_cast<GetClassObject>(GetProcAddress(module, "DllGetClassObject"));
    auto can_unload = reinterpret_cast<CanUnload>(GetProcAddress(module, "DllCanUnloadNow"));
    auto native_source_sha256 = reinterpret_cast<NativeSourceSha256>(
        GetProcAddress(module, "SkillMagnetNativeSourceSha256"));
    if (!get_class || !can_unload || !native_source_sha256) return 4;
    const wchar_t* source_digest_pointer = native_source_sha256();
    const std::wstring source_digest =
        source_digest_pointer ? source_digest_pointer : L"";
    const bool native_source_bound =
        NativeSourceManifestMatches(source_manifest, source_digest);

    IClassFactory* factory = nullptr;
    if (FAILED(get_class(CLSID_SkillMagnetCommand, IID_IClassFactory,
                         reinterpret_cast<void**>(&factory)))) return 5;
    IExplorerCommand* command = nullptr;
    if (FAILED(factory->CreateInstance(nullptr, __uuidof(IExplorerCommand),
                                       reinterpret_cast<void**>(&command)))) return 6;

    PWSTR title = nullptr;
    EXPCMDSTATE state{};
    EXPCMDFLAGS flags{};
    GUID canonical{};
    const bool valid = SUCCEEDED(command->GetTitle(nullptr, &title)) &&
        title && wcscmp(title, L"Skill Magnet") == 0 &&
        SUCCEEDED(command->GetState(nullptr, FALSE, &state)) && state == ECS_ENABLED &&
        SUCCEEDED(command->GetFlags(&flags)) && flags == ECF_DEFAULT &&
        SUCCEEDED(command->GetCanonicalName(&canonical)) &&
        canonical == CLSID_SkillMagnetCommand;
    CoTaskMemFree(title);
    IEnumExplorerCommand* unexpected_children = nullptr;
    const bool no_subcommands =
        command->EnumSubCommands(&unexpected_children) == E_NOTIMPL && !unexpected_children;
    if (unexpected_children) unexpected_children->Release();
    const bool enumeration_is_silent =
        GetFileAttributesW(invoke_log.c_str()) == INVALID_FILE_ATTRIBUTES;

    wchar_t executable_buffer[32768]{};
    const DWORD executable_size = GetModuleFileNameW(
        nullptr, executable_buffer, static_cast<DWORD>(_countof(executable_buffer)));
    if (!executable_size || executable_size >= _countof(executable_buffer)) return 17;
    const std::wstring contract_executable(executable_buffer, executable_size);
    const std::wstring selected_folder =
        local_app_data + L"\\selected folder \u65e5\u672c\u8a9e";
    const std::wstring background_folder =
        local_app_data + L"\\background folder \u65e5\u672c\u8a9e";
    if (!CreateDirectoryW(selected_folder.c_str(), nullptr) ||
        !CreateDirectoryW(background_folder.c_str(), nullptr)) return 18;
    const std::wstring selected_probe_output = local_app_data + L"\\selected-argv.txt";
    const std::wstring background_probe_output = local_app_data + L"\\background-argv.txt";

    IShellItem* selected_item = nullptr;
    IShellItemArray* selected_items = nullptr;
    if (FAILED(SHCreateItemFromParsingName(
            selected_folder.c_str(), nullptr, IID_PPV_ARGS(&selected_item))) ||
        FAILED(SHCreateShellItemArrayFromShellItem(
            selected_item, IID_PPV_ARGS(&selected_items)))) return 13;

    IExplorerCommand* selected_probe_command = nullptr;
    const bool selected_probe_loaded = CreateCommandForManifest(
        manifest_path, ProbeManifest(contract_executable, selected_probe_output),
        factory, &selected_probe_command);
    const bool root_selected_invoke = selected_probe_loaded &&
        SUCCEEDED(selected_probe_command->Invoke(selected_items, nullptr));
    const bool selected_argument_exact =
        ReadWideText(selected_probe_output) == selected_folder;
    if (selected_probe_command) selected_probe_command->Release();

    PIDLIST_ABSOLUTE selected_folder_id = nullptr;
    PIDLIST_ABSOLUTE folder_id = nullptr;
    SFGAOF folder_attributes = 0;
    if (FAILED(SHParseDisplayName(selected_folder.c_str(), nullptr,
                                  &selected_folder_id, 0, &folder_attributes))) return 14;
    if (FAILED(SHParseDisplayName(background_folder.c_str(), nullptr,
                                  &folder_id, 0, &folder_attributes))) {
        ILFree(selected_folder_id);
        return 14;
    }
    PCIDLIST_ABSOLUTE multiple_folder_ids[] = {selected_folder_id, folder_id};
    IShellItemArray* multiple_items = nullptr;
    const bool multiple_array_created = SUCCEEDED(SHCreateShellItemArrayFromIDLists(
        static_cast<UINT>(_countof(multiple_folder_ids)), multiple_folder_ids,
        &multiple_items)) && multiple_items != nullptr;
    auto* test_site = new TestFolderViewSite(folder_id);
    ILFree(selected_folder_id);
    ILFree(folder_id);
    IExplorerCommand* background_probe_command = nullptr;
    const bool background_probe_loaded = CreateCommandForManifest(
        manifest_path, ProbeManifest(contract_executable, background_probe_output),
        factory, &background_probe_command);
    IObjectWithSite* site_aware = nullptr;
    const bool site_supported = background_probe_loaded &&
        SUCCEEDED(background_probe_command->QueryInterface(
            IID_IObjectWithSite, reinterpret_cast<void**>(&site_aware))) && site_aware;
    const bool site_set = site_supported &&
        SUCCEEDED(site_aware->SetSite(static_cast<IServiceProvider*>(test_site)));
    test_site->Release();
    const bool root_background_invoke = site_set &&
        SUCCEEDED(background_probe_command->Invoke(nullptr, nullptr));
    const bool background_argument_exact =
        ReadWideText(background_probe_output) == background_folder;
    if (site_aware) {
        site_aware->SetSite(nullptr);
        site_aware->Release();
    }
    if (background_probe_command) background_probe_command->Release();
    const bool probe_manifest_restored = WriteBytes(manifest_path, original_manifest);
    const bool multiple_selection_rejected = multiple_array_created &&
        FAILED(command->Invoke(multiple_items, nullptr));
    if (multiple_items) multiple_items->Release();

    static constexpr char failure_manifest[] =
        "skill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
        "Immediate failure contract probe.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 7 "
        "__SKILL_MAGNET_PROJECT__\r\n";
    static constexpr char invalid_extra_manifest[] =
        "skill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
        "Valid launcher followed by invalid data.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 0 "
        "__SKILL_MAGNET_PROJECT__\r\n"
        "invalid-extra-line\r\n";
    static constexpr char duplicate_launcher_manifest[] =
        "skill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tFirst.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 0 "
        "__SKILL_MAGNET_PROJECT__\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tSecond.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 0 "
        "__SKILL_MAGNET_PROJECT__\r\n";
    static constexpr char unknown_kind_manifest[] =
        "skill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tpackage\troot\tSkill Magnet\tUnknown kind.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 0 "
        "__SKILL_MAGNET_PROJECT__\r\n";
    static constexpr char bom_header_manifest[] =
        "\xef\xbb\xbfskill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tBOM header.\t"
        "\"C:\\Windows\\System32\\cmd.exe\" /d /c exit 0 "
        "__SKILL_MAGNET_PROJECT__\r\n";
    IExplorerCommand* failure_command = nullptr;
    const bool failure_manifest_written = WriteBytes(manifest_path, failure_manifest);
    const bool failure_command_loaded = failure_manifest_written &&
        SUCCEEDED(factory->CreateInstance(nullptr, __uuidof(IExplorerCommand),
                                          reinterpret_cast<void**>(&failure_command))) &&
        failure_command != nullptr;
    const bool invalid_extra_rejected = ManifestCreatesDisabledRoot(
        manifest_path, invalid_extra_manifest, factory);
    const bool duplicate_launcher_rejected = ManifestCreatesDisabledRoot(
        manifest_path, duplicate_launcher_manifest, factory);
    const bool unknown_kind_rejected = ManifestCreatesDisabledRoot(
        manifest_path, unknown_kind_manifest, factory);
    const bool bom_header_rejected = ManifestCreatesDisabledRoot(
        manifest_path, bom_header_manifest, factory);
    const bool manifest_restored = WriteBytes(manifest_path, original_manifest);
    const bool immediate_failure_detected = failure_command_loaded &&
        FAILED(failure_command->Invoke(selected_items, nullptr));
    if (failure_command) failure_command->Release();
    factory->Release();

    selected_items->Release();
    selected_item->Release();

    const std::wstring log = ReadWideText(invoke_log);
    std::wstring selected_digest;
    std::wstring background_digest;
    const bool selected_evidence =
        FindSelectionDigest(log, L"selected_item", &selected_digest);
    const bool background_evidence =
        FindSelectionDigest(log, L"background_site", &background_digest);
    const bool project_digests_exact =
        selected_digest == ExpectedProjectDigest(selected_folder) &&
        background_digest == ExpectedProjectDigest(background_folder);
    const bool path_is_private =
        log.find(selected_folder) == std::wstring::npos &&
        log.find(background_folder) == std::wstring::npos &&
        log.find(local_app_data) == std::wstring::npos;
    const bool launch_evidence =
        log.find(L"event=child_exited") != std::wstring::npos &&
        log.find(L"event=child_process_failed") != std::wstring::npos &&
        log.find(L"detail=7") != std::wstring::npos;
    const bool recovery_messages_actionable = RecoveryMessagesAreActionable();

    command->Release();
    const bool unloadable = can_unload() == S_OK;
    FreeLibrary(module);
    DeleteFileW(invoke_log.c_str());
    DeleteFileW(selected_probe_output.c_str());
    DeleteFileW(background_probe_output.c_str());
    RemoveDirectoryW(selected_folder.c_str());
    RemoveDirectoryW(background_folder.c_str());
    RemoveDirectoryW((local_app_data + L"\\SkillMagnet\\ContextMenu").c_str());
    RemoveDirectoryW((local_app_data + L"\\SkillMagnet").c_str());
    RemoveDirectoryW(local_app_data.c_str());
    SetEnvironmentVariableW(L"SKILL_MAGNET_NATIVE_CONTRACT_TEST", nullptr);
    CoUninitialize();
    if (!valid || !no_subcommands || !enumeration_is_silent || !site_supported || !site_set ||
        !selected_probe_loaded || !background_probe_loaded ||
        !root_selected_invoke || !root_background_invoke ||
        !selected_argument_exact || !background_argument_exact ||
        !probe_manifest_restored || !multiple_array_created ||
        !multiple_selection_rejected || !failure_manifest_written ||
        !failure_command_loaded || !manifest_restored || !immediate_failure_detected ||
        !invalid_extra_rejected || !duplicate_launcher_rejected || !unknown_kind_rejected ||
        !bom_header_rejected ||
        !selected_evidence || !background_evidence || selected_digest == background_digest ||
        !project_digests_exact ||
        !path_is_private || !launch_evidence || !recovery_messages_actionable ||
        !native_source_bound || !unloadable) {
        return 7;
    }
    std::wcout << L"SkillMagnet direct-root IExplorerCommand contract PASS\n";
    return 0;
}

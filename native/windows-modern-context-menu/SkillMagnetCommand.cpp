#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <ocidl.h>
#include <servprov.h>
#include <shellapi.h>
#include <shobjidl.h>

#include <atomic>
#include <new>
#include <string>
#include <utility>
#include <vector>

#include "RecoveryMessages.h"

// {13E2A9DD-4378-4F9D-A385-973C61B19E63}
static const CLSID CLSID_SkillMagnetCommand = {
    0x13e2a9dd, 0x4378, 0x4f9d, {0xa3, 0x85, 0x97, 0x3c, 0x61, 0xb1, 0x9e, 0x63}};
static constexpr wchar_t kProjectMarker[] = L"__SKILL_MAGNET_PROJECT__";
static std::atomic<long> g_object_count{0};
static HMODULE g_module = nullptr;

static std::wstring InvokeLogPath() {
    std::vector<wchar_t> local_app_data(32768);
    const DWORD size = GetEnvironmentVariableW(
        L"LOCALAPPDATA", local_app_data.data(), static_cast<DWORD>(local_app_data.size()));
    if (!size || size >= local_app_data.size()) return {};
    std::wstring product_root(local_app_data.data(), size);
    product_root += L"\\SkillMagnet";
    CreateDirectoryW(product_root.c_str(), nullptr);
    product_root += L"\\ContextMenu";
    CreateDirectoryW(product_root.c_str(), nullptr);
    return product_root + L"\\invoke.log";
}

static std::wstring Sha256Digest(const std::wstring& value) {
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
        return L"unavailable";
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
    return result.empty() ? L"unavailable" : result;
}

static std::wstring NewInvocationId() noexcept {
    GUID value{};
    if (FAILED(CoCreateGuid(&value))) return L"unavailable";
    wchar_t token[33]{};
    const int length = swprintf_s(
        token, L"%08x%04x%04x%02x%02x%02x%02x%02x%02x%02x%02x",
        value.Data1, value.Data2, value.Data3, value.Data4[0], value.Data4[1],
        value.Data4[2], value.Data4[3], value.Data4[4], value.Data4[5],
        value.Data4[6], value.Data4[7]);
    return length == 32 ? std::wstring(token, 32) : L"unavailable";
}

static DWORD LogInvokeEvent(const wchar_t* event, const std::wstring& command_digest,
                           DWORD detail = 0, const wchar_t* selection_source = L"none",
                           const wchar_t* project_digest = L"none",
                           const wchar_t* invocation_id = L"none") noexcept {
    const std::wstring path = InvokeLogPath();
    if (path.empty()) return ERROR_PATH_NOT_FOUND;
    SYSTEMTIME timestamp{};
    GetSystemTime(&timestamp);
    wchar_t line[512]{};
    const int length = swprintf_s(
        line, L"%04u-%02u-%02uT%02u:%02u:%02u.%03uZ\tevent=%ls\tcommand_sha256=%ls"
              L"\tdetail=%lu\tselection_source=%ls\tproject_sha256=%ls"
              L"\tinvocation_id=%ls\r\n",
        timestamp.wYear, timestamp.wMonth, timestamp.wDay, timestamp.wHour,
        timestamp.wMinute, timestamp.wSecond, timestamp.wMilliseconds, event,
        command_digest.c_str(), static_cast<unsigned long>(detail), selection_source,
        project_digest, invocation_id);
    if (length <= 0) return ERROR_INVALID_DATA;
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return GetLastError();
    DWORD written = 0;
    const DWORD expected = static_cast<DWORD>(length * sizeof(wchar_t));
    const BOOL write_ok = WriteFile(file, line, expected, &written, nullptr);
    const DWORD write_error = !write_ok
        ? GetLastError()
        : (written == expected ? ERROR_SUCCESS : ERROR_WRITE_FAULT);
    const BOOL flush_ok = write_error == ERROR_SUCCESS && FlushFileBuffers(file);
    const DWORD flush_error = flush_ok ? ERROR_SUCCESS : GetLastError();
    CloseHandle(file);
    return write_error != ERROR_SUCCESS ? write_error : flush_error;
}

static HRESULT CopyString(const std::wstring& value, PWSTR* output) noexcept {
    if (!output) return E_POINTER;
    *output = nullptr;
    const size_t bytes = (value.size() + 1) * sizeof(wchar_t);
    auto* copy = static_cast<PWSTR>(CoTaskMemAlloc(bytes));
    if (!copy) return E_OUTOFMEMORY;
    memcpy(copy, value.c_str(), bytes);
    *output = copy;
    return S_OK;
}

static std::wstring Utf8ToWide(const std::string& value) {
    if (value.empty()) return {};
    const int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                         value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (size <= 0) return {};
    std::wstring result(static_cast<size_t>(size), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), result.data(), size) != size) return {};
    return result;
}

static std::wstring ModuleDirectory() {
    std::vector<wchar_t> buffer(32768);
    const DWORD size = GetModuleFileNameW(g_module, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!size || size >= buffer.size()) return {};
    std::wstring path(buffer.data(), size);
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? std::wstring() : path.substr(0, slash);
}

static std::string ReadMenuManifest() {
    std::wstring path = ModuleDirectory() + L"\\SkillMagnetMenu.tsv";
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return {};
    LARGE_INTEGER length{};
    if (!GetFileSizeEx(file, &length) || length.QuadPart <= 0 || length.QuadPart > 8 * 1024 * 1024) {
        CloseHandle(file);
        return {};
    }
    std::string data(static_cast<size_t>(length.QuadPart), '\0');
    DWORD read = 0;
    const BOOL ok = ReadFile(file, data.data(), static_cast<DWORD>(data.size()), &read, nullptr);
    CloseHandle(file);
    if (!ok || read != data.size()) return {};
    return data;
}

static std::vector<std::wstring> SplitFields(const std::string& line) {
    std::vector<std::wstring> result;
    size_t start = 0;
    while (true) {
        const size_t end = line.find('\t', start);
        result.push_back(Utf8ToWide(line.substr(start, end - start)));
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return result;
}

static std::wstring QuoteArgument(const std::wstring& value) {
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

static std::wstring WindowsErrorDetail(DWORD error) {
    wchar_t* raw = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, error, 0, reinterpret_cast<PWSTR>(&raw), 0, nullptr);
    std::wstring detail = length && raw ? std::wstring(raw, length) : L"Unknown Windows error";
    if (raw) LocalFree(raw);
    while (!detail.empty() &&
           (detail.back() == L'\r' || detail.back() == L'\n' || detail.back() == L' ')) {
        detail.pop_back();
    }
    return L"Windows error " + std::to_wstring(error) + L": " + detail;
}

static std::wstring ConfigPathFromCommand(const std::wstring& command) {
    int argument_count = 0;
    LPWSTR* arguments = CommandLineToArgvW(command.c_str(), &argument_count);
    if (!arguments) return {};
    std::wstring config;
    for (int index = 1; index + 1 < argument_count; ++index) {
        if (wcscmp(arguments[index], L"--config") == 0) {
            config = arguments[index + 1];
            break;
        }
    }
    LocalFree(arguments);
    return config;
}

static void ShowRecoverableError(const std::wstring& message) noexcept {
    wchar_t contract_test[2]{};
    if (GetEnvironmentVariableW(
            L"SKILL_MAGNET_NATIVE_CONTRACT_TEST", contract_test,
            static_cast<DWORD>(2)) > 0) {
        return;
    }
    MessageBoxW(nullptr, message.c_str(), L"Skill Magnet", MB_OK | MB_ICONERROR);
}

static HRESULT ShellItemPath(IShellItem* item, std::wstring* path) {
    if (!item || !path) return E_INVALIDARG;
    PWSTR raw = nullptr;
    const HRESULT result = item->GetDisplayName(SIGDN_FILESYSPATH, &raw);
    if (SUCCEEDED(result) && raw) *path = raw;
    CoTaskMemFree(raw);
    return result;
}

static HRESULT SiteItem(IUnknown* site, IShellItem** item) {
    if (!site || !item) return E_INVALIDARG;
    *item = nullptr;
    IServiceProvider* services = nullptr;
    HRESULT result = site->QueryInterface(IID_PPV_ARGS(&services));
    if (FAILED(result)) return result;
    IFolderView* view = nullptr;
    result = services->QueryService(SID_SFolderView, IID_PPV_ARGS(&view));
    services->Release();
    if (FAILED(result)) return result;
    IPersistFolder2* folder = nullptr;
    result = view->GetFolder(IID_PPV_ARGS(&folder));
    view->Release();
    if (FAILED(result)) return result;
    PIDLIST_ABSOLUTE folder_id = nullptr;
    result = folder->GetCurFolder(&folder_id);
    folder->Release();
    if (FAILED(result)) return result;
    result = SHCreateItemFromIDList(folder_id, IID_PPV_ARGS(item));
    CoTaskMemFree(folder_id);
    return result;
}

static HRESULT SitePath(IUnknown* site, std::wstring* path) {
    if (!path) return E_POINTER;
    IShellItem* folder_item = nullptr;
    const HRESULT result = SiteItem(site, &folder_item);
    if (FAILED(result)) return result;
    const HRESULT path_result = ShellItemPath(folder_item, path);
    folder_item->Release();
    return path_result;
}

static HRESULT SelectedPath(IShellItemArray* items, IUnknown* site,
                            std::wstring* path, bool* background) {
    if (!path || !background) return E_POINTER;
    *background = false;
    DWORD item_count = 0;
    const HRESULT count_result = items ? items->GetCount(&item_count) : S_OK;
    if (FAILED(count_result)) return count_result;
    if (!items || item_count == 0) {
        const HRESULT result = SitePath(site, path);
        if (SUCCEEDED(result)) *background = true;
        return result;
    }
    if (item_count != 1) return E_INVALIDARG;
    IShellItem* item = nullptr;
    HRESULT result = items->GetItemAt(0, &item);
    if (FAILED(result)) return result;
    result = ShellItemPath(item, path);
    if (FAILED(result)) {
        item->Release();
        return result;
    }
    IShellItem* site_item = nullptr;
    if (SUCCEEDED(SiteItem(site, &site_item))) {
        int comparison = 1;
        const HRESULT comparison_result = item->Compare(
            site_item, SICHINT_CANONICAL, &comparison);
        site_item->Release();
        if (SUCCEEDED(comparison_result) && comparison == 0) {
            const HRESULT site_result = SitePath(site, path);
            if (SUCCEEDED(site_result)) *background = true;
        }
    }
    item->Release();
    return result;
}

class MenuNode final : public IExplorerCommand, public IObjectWithSite {
public:
    MenuNode(std::wstring title, std::wstring command = {}, bool root = false)
        : title_(std::move(title)), command_(std::move(command)), root_(root) { ++g_object_count; }
    ~MenuNode() {
        if (site_) site_->Release();
        --g_object_count;
    }
    void SetTitle(std::wstring title) { title_ = std::move(title); }
    void SetCommand(std::wstring command) { command_ = std::move(command); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (iid == IID_IUnknown || iid == __uuidof(IExplorerCommand)) {
            *value = static_cast<IExplorerCommand*>(this);
            AddRef();
            return S_OK;
        }
        if (iid == IID_IObjectWithSite) {
            *value = static_cast<IObjectWithSite*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG count = --references_;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE GetTitle(IShellItemArray*, PWSTR* title) override {
        return CopyString(title_, title);
    }
    HRESULT STDMETHODCALLTYPE GetIcon(IShellItemArray*, PWSTR* icon) override {
        if (!icon) return E_POINTER;
        *icon = nullptr;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetToolTip(IShellItemArray*, PWSTR* tip) override {
        if (!tip) return E_POINTER;
        *tip = nullptr;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetCanonicalName(GUID* name) override {
        if (!name) return E_POINTER;
        // The extension root has the registered stable identity. Returning the
        // same canonical GUID for every dynamic child makes Explorer collapse
        // distinct nested commands into one cached item.
        *name = root_ ? CLSID_SkillMagnetCommand : GUID_NULL;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetState(IShellItemArray*, BOOL, EXPCMDSTATE* state) override {
        if (!state) return E_POINTER;
        *state = command_.empty() ? ECS_DISABLED : ECS_ENABLED;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(IShellItemArray* items, IBindCtx*) override {
        const std::wstring template_digest = Sha256Digest(command_);
        const std::wstring invocation_id = NewInvocationId();
        // Target resolution calls Explorer COM APIs. Record receipt before
        // touching them so a hung or failing provider cannot make a user
        // invocation disappear from the diagnostic log.
        const DWORD enter_log_error = LogInvokeEvent(
            L"invoke_enter", template_digest, 0, L"unresolved",
            L"unavailable", invocation_id.c_str());
        if (enter_log_error != ERROR_SUCCESS) {
            ShowRecoverableError(
                L"右クリックの診断記録を開始できないため、安全に実行を中止しました。\n\n"
                L"原因:\n診断ログを開くか書き込む処理がWindowsエラー " +
                std::to_wstring(enter_log_error) +
                L" で失敗しました。\n\n復旧手順:\n"
                L"1. invoke.logを開いているアプリと他のSkill Magnet処理を閉じます。\n"
                L"2. 元のフォルダーをもう一度右クリックします。\n\n"
                L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log");
            return HRESULT_FROM_WIN32(enter_log_error);
        }
        std::wstring project;
        bool background = false;
        const HRESULT result = SelectedPath(items, site_, &project, &background);
        // Explorer may represent Directory\Background as null, an empty array,
        // or a one-item array containing the current folder itself.
        const wchar_t* selection_source =
            background ? L"background_site" : L"selected_item";
        if (command_.empty()) {
            LogInvokeEvent(L"command_empty", template_digest, 0, selection_source,
                           L"unavailable", invocation_id.c_str());
            ShowRecoverableError(
                L"右クリックメニューの実行commandが空のため、実行を開始できませんでした。\n\n"
                L"復旧手順:\n1. Skill Magnetの右クリックメニューを再登録します。\n"
                L"2. usable_installed_state=trueを確認してから再実行します。\n\n"
                L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log");
            return E_INVALIDARG;
        }
        std::wstring project_digest = L"unavailable";
        if (FAILED(result)) {
            LogInvokeEvent(L"selection_failed", template_digest,
                           static_cast<DWORD>(result), selection_source, L"unavailable",
                           invocation_id.c_str());
        } else {
            project_digest = Sha256Digest(project);
            LogInvokeEvent(L"selection_succeeded", template_digest, 0,
                           selection_source, project_digest.c_str(), invocation_id.c_str());
        }
        const size_t marker = command_.find(kProjectMarker);
        if (FAILED(result)) {
            ShowRecoverableError(
                L"右クリックしたフォルダーを特定できませんでした。\n\n"
                L"原因:\n複数項目の選択、またはExplorerからフォルダー情報を取得できない状態です。\n\n"
                L"復旧手順:\n"
                L"1. 対象フォルダーをFile Explorerで開きます。\n"
                L"2. フォルダー1件だけ、または開いたフォルダー内の余白を右クリックします。\n"
                L"3. 「Skill Magnet」をもう一度押します。\n\n"
                L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log\n"
                L"このfileをメモ帳で開き、event=selection_failedを探してください。");
            return result;
        }
        if (marker == std::wstring::npos) {
            LogInvokeEvent(L"marker_missing", template_digest, 0, selection_source,
                           project_digest.c_str(), invocation_id.c_str());
            const std::wstring config_path = ConfigPathFromCommand(command_);
            const std::wstring repair_command =
                SkillMagnetRecovery::InstalledPythonRepairCommand(config_path);
            ShowRecoverableError(
                L"登録済みのSkill Magnetメニューが壊れているため、実行を開始できませんでした。\n\n"
                L"原因:\n右クリックしたフォルダーを安全に渡すmarkerがmenu commandにありません。\n\n"
                L"復旧手順:\n1. この画面を開いたままCtrl+Cを押して内容をコピーします。\n"
                L"2. Windows TerminalのPowerShellタブへ、次の正確な修復・再登録commandを"
                L"貼り付けて実行します:\n" + repair_command +
                L"\n3. usable_installed_state=trueを確認してから、元の操作を一度再実行します。\n\n"
                L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log\n"
                L"このfileをメモ帳で開き、event=marker_missingを探してください。");
            return E_INVALIDARG;
        }
        std::wstring launch = command_;
        launch.replace(marker, wcslen(kProjectMarker), QuoteArgument(project));
        const std::wstring config_path = ConfigPathFromCommand(command_);
        const std::wstring repair_command =
            SkillMagnetRecovery::InstalledPythonRepairCommand(config_path);
        const std::wstring launch_digest = Sha256Digest(launch);
        std::vector<wchar_t> mutable_command(launch.begin(), launch.end());
        mutable_command.push_back(L'\0');
        STARTUPINFOW startup{sizeof(startup)};
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE,
                            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, nullptr,
                            nullptr,
                            &startup, &process)) {
            const DWORD error = GetLastError();
            LogInvokeEvent(L"create_process_failed", launch_digest, error, selection_source,
                           project_digest.c_str(), invocation_id.c_str());
            const std::wstring message = SkillMagnetRecovery::CreateProcessFailureMessage(
                error, WindowsErrorDetail(error), repair_command);
            ShowRecoverableError(message);
            return HRESULT_FROM_WIN32(error);
        }
        LogInvokeEvent(L"create_process_succeeded", launch_digest, process.dwProcessId,
                       selection_source, project_digest.c_str(), invocation_id.c_str());
        CloseHandle(process.hThread);
        constexpr DWORD kImmediateExitWindowMilliseconds = 1200;
        const DWORD wait = WaitForSingleObject(
            process.hProcess, kImmediateExitWindowMilliseconds);
        if (wait == WAIT_FAILED) {
            const DWORD error = GetLastError();
            LogInvokeEvent(L"child_wait_failed", launch_digest, error, selection_source,
                           project_digest.c_str(), invocation_id.c_str());
            CloseHandle(process.hProcess);
            const std::wstring message = SkillMagnetRecovery::WaitFailureMessage(
                WindowsErrorDetail(error), repair_command);
            ShowRecoverableError(message);
            return HRESULT_FROM_WIN32(error);
        }
        if (wait == WAIT_OBJECT_0) {
            DWORD exit_code = 0;
            if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
                const DWORD error = GetLastError();
                LogInvokeEvent(L"child_exit_read_failed", launch_digest, error,
                               selection_source, project_digest.c_str(),
                               invocation_id.c_str());
                CloseHandle(process.hProcess);
                const std::wstring message = SkillMagnetRecovery::WaitFailureMessage(
                    WindowsErrorDetail(error), repair_command);
                ShowRecoverableError(message);
                return HRESULT_FROM_WIN32(error);
            }
            LogInvokeEvent(L"child_exited", launch_digest, exit_code, selection_source,
                           project_digest.c_str(), invocation_id.c_str());
            CloseHandle(process.hProcess);
            if (exit_code != 0) {
                const std::wstring message =
                    SkillMagnetRecovery::ImmediateExitFailureMessage(
                        exit_code, repair_command);
                LogInvokeEvent(L"child_process_failed", launch_digest, exit_code,
                               selection_source, project_digest.c_str(),
                               invocation_id.c_str());
                ShowRecoverableError(message);
                return HRESULT_FROM_WIN32(exit_code);
            }
            return S_OK;
        }
        LogInvokeEvent(L"child_running", launch_digest, process.dwProcessId,
                       selection_source, project_digest.c_str(), invocation_id.c_str());
        CloseHandle(process.hProcess);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetFlags(EXPCMDFLAGS* flags) override {
        if (!flags) return E_POINTER;
        *flags = ECF_DEFAULT;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE EnumSubCommands(IEnumExplorerCommand** commands) override {
        if (!commands) return E_POINTER;
        *commands = nullptr;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE SetSite(IUnknown* site) override {
        if (site) site->AddRef();
        IUnknown* previous = site_;
        site_ = site;
        if (previous) previous->Release();
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetSite(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        return site_ ? site_->QueryInterface(iid, value) : E_FAIL;
    }
private:
    std::atomic<ULONG> references_{1};
    std::wstring title_;
    std::wstring command_;
    IUnknown* site_{};
    bool root_{};
};

static MenuNode* LoadRoot() {
    auto* root = new (std::nothrow) MenuNode(L"Skill Magnet", L"", true);
    if (!root) return nullptr;
    const std::string data = ReadMenuManifest();
    size_t start = 0;
    bool header_seen = false;
    bool contract_valid = true;
    bool launcher_seen = false;
    size_t record_count = 0;
    while (start < data.size()) {
        size_t end = data.find('\n', start);
        if (end == std::string::npos) end = data.size();
        std::string line = data.substr(start, end - start);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        start = end + 1;
        if (!header_seen) {
            header_seen = line == "skill-magnet-menu-v4";
            if (!header_seen) {
                contract_valid = false;
                break;
            }
            continue;
        }
        if (line.empty()) continue;
        ++record_count;
        const auto fields = SplitFields(line);
        if (fields.size() != 7 || fields[0].empty() || fields[1] != L"Skill Magnet" ||
            fields[0] != L"__launcher__" || fields[2] != L"launcher" ||
            fields[3] != L"root" || fields[4] != L"Skill Magnet" ||
            fields[5].empty()) {
            contract_valid = false;
            break;
        }
        const size_t marker = fields[6].find(kProjectMarker);
        if (marker == std::wstring::npos ||
            fields[6].find(kProjectMarker, marker + wcslen(kProjectMarker)) !=
                std::wstring::npos) {
            contract_valid = false;
            break;
        }
        if (launcher_seen) {
            contract_valid = false;
            break;
        }
        root->SetCommand(fields[6]);
        launcher_seen = true;
    }
    if (!header_seen || !contract_valid || !launcher_seen || record_count != 1) {
        root->SetCommand({});
    }
    return root;
}

class SkillMagnetClassFactory final : public IClassFactory {
public:
    SkillMagnetClassFactory() noexcept { ++g_object_count; }
    ~SkillMagnetClassFactory() { --g_object_count; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (iid == IID_IUnknown || iid == IID_IClassFactory) {
            *value = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG count = --references_;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** value) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* command = LoadRoot();
        if (!command) return E_OUTOFMEMORY;
        const HRESULT result = command->QueryInterface(iid, value);
        command->Release();
        return result;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override {
        g_object_count += lock ? 1 : -1;
        return S_OK;
    }
private:
    std::atomic<ULONG> references_{1};
};

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** value) {
    if (clsid != CLSID_SkillMagnetCommand) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) SkillMagnetClassFactory();
    if (!factory) return E_OUTOFMEMORY;
    const HRESULT result = factory->QueryInterface(iid, value);
    factory->Release();
    return result;
}
STDAPI DllCanUnloadNow() { return g_object_count == 0 ? S_OK : S_FALSE; }
static constexpr wchar_t kNativeSourceBinding[] =
    L"skill-magnet-native-source-v1:" SKILL_MAGNET_NATIVE_SOURCE_SHA256;
extern "C" __declspec(dllexport) const wchar_t* WINAPI
SkillMagnetNativeSourceSha256() noexcept {
    return kNativeSourceBinding +
        (_countof(L"skill-magnet-native-source-v1:") - 1);
}
BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

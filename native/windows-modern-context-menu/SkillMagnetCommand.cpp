#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shobjidl.h>

#include <atomic>
#include <new>
#include <string>
#include <utility>
#include <vector>

// {13E2A9DD-4378-4F9D-A385-973C61B19E63}
static const CLSID CLSID_SkillMagnetCommand = {
    0x13e2a9dd, 0x4378, 0x4f9d, {0xa3, 0x85, 0x97, 0x3c, 0x61, 0xb1, 0x9e, 0x63}};
static constexpr wchar_t kProjectMarker[] = L"__SKILL_MAGNET_PROJECT__";
static std::atomic<long> g_object_count{0};
static HMODULE g_module = nullptr;

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
    // A sparse package gives the COM server package identity, so
    // GetModuleFileNameW may report the virtual WindowsApps package path even
    // though the mutable menu contract lives in the registered external
    // location. Resolve that product-owned location explicitly as a fallback.
    if (file == INVALID_HANDLE_VALUE) {
        std::vector<wchar_t> local_app_data(32768);
        const DWORD size = GetEnvironmentVariableW(
            L"LOCALAPPDATA", local_app_data.data(), static_cast<DWORD>(local_app_data.size()));
        if (size > 0 && size < local_app_data.size()) {
            path.assign(local_app_data.data(), size);
            path += L"\\SkillMagnet\\ContextMenu\\SkillMagnetMenu.tsv";
            file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        }
    }
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

static HRESULT SelectedPath(IShellItemArray* items, std::wstring* path) {
    if (!items || !path) return E_INVALIDARG;
    DWORD count = 0;
    if (FAILED(items->GetCount(&count)) || count != 1) return E_INVALIDARG;
    IShellItem* item = nullptr;
    HRESULT result = items->GetItemAt(0, &item);
    if (FAILED(result)) return result;
    PWSTR raw = nullptr;
    result = item->GetDisplayName(SIGDN_FILESYSPATH, &raw);
    item->Release();
    if (SUCCEEDED(result) && raw) *path = raw;
    CoTaskMemFree(raw);
    return result;
}

class MenuNode;

class MenuEnumerator final : public IEnumExplorerCommand {
public:
    explicit MenuEnumerator(std::vector<MenuNode*> commands, ULONG index = 0) noexcept;
    ~MenuEnumerator();
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override;
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Next(ULONG count, IExplorerCommand** commands, ULONG* fetched) override;
    HRESULT STDMETHODCALLTYPE Skip(ULONG count) override;
    HRESULT STDMETHODCALLTYPE Reset() override { index_ = 0; return S_OK; }
    HRESULT STDMETHODCALLTYPE Clone(IEnumExplorerCommand** result) override;
private:
    std::atomic<ULONG> references_{1};
    std::vector<MenuNode*> commands_;
    ULONG index_{};
};

class MenuNode final : public IExplorerCommand {
public:
    MenuNode(std::wstring title, std::wstring command = {}, bool root = false)
        : title_(std::move(title)), command_(std::move(command)), root_(root) { ++g_object_count; }
    ~MenuNode() {
        for (auto* child : children_) child->Release();
        --g_object_count;
    }
    MenuNode* FindOrAdd(const std::wstring& title) {
        for (auto* child : children_) if (child->title_ == title) return child;
        auto* child = new (std::nothrow) MenuNode(title);
        if (child) children_.push_back(child);
        return child;
    }
    bool AddLeaf(const std::wstring& title, const std::wstring& command) {
        auto* child = new (std::nothrow) MenuNode(title, command);
        if (!child) return false;
        children_.push_back(child);
        return true;
    }
    void SetTitle(std::wstring title) { title_ = std::move(title); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (iid == IID_IUnknown || iid == __uuidof(IExplorerCommand)) {
            *value = static_cast<IExplorerCommand*>(this);
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
        *state = (!children_.empty() || !command_.empty()) ? ECS_ENABLED : ECS_DISABLED;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke(IShellItemArray* items, IBindCtx*) override {
        if (command_.empty()) return E_NOTIMPL;
        std::wstring project;
        HRESULT result = SelectedPath(items, &project);
        const size_t marker = command_.find(kProjectMarker);
        if (FAILED(result) || marker == std::wstring::npos) {
            MessageBoxW(nullptr, L"Skill Magnet could not resolve the selected folder.",
                        L"Skill Magnet", MB_OK | MB_ICONERROR);
            return FAILED(result) ? result : E_INVALIDARG;
        }
        std::wstring launch = command_;
        launch.replace(marker, wcslen(kProjectMarker), QuoteArgument(project));
        std::vector<wchar_t> mutable_command(launch.begin(), launch.end());
        mutable_command.push_back(L'\0');
        STARTUPINFOW startup{sizeof(startup)};
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE,
                            CREATE_UNICODE_ENVIRONMENT, nullptr, project.c_str(),
                            &startup, &process)) {
            MessageBoxW(nullptr, L"Skill Magnet could not start the selected AI task.",
                        L"Skill Magnet", MB_OK | MB_ICONERROR);
            return HRESULT_FROM_WIN32(GetLastError());
        }
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetFlags(EXPCMDFLAGS* flags) override {
        if (!flags) return E_POINTER;
        *flags = children_.empty() ? ECF_DEFAULT : ECF_HASSUBCOMMANDS;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE EnumSubCommands(IEnumExplorerCommand** commands) override {
        if (!commands) return E_POINTER;
        *commands = nullptr;
        if (children_.empty()) return E_NOTIMPL;
        auto* enumerator = new (std::nothrow) MenuEnumerator(children_);
        if (!enumerator) return E_OUTOFMEMORY;
        *commands = enumerator;
        return S_OK;
    }
private:
    std::atomic<ULONG> references_{1};
    std::wstring title_;
    std::wstring command_;
    std::vector<MenuNode*> children_;
    bool root_{};
};

MenuEnumerator::MenuEnumerator(std::vector<MenuNode*> commands, ULONG index) noexcept
    : commands_(std::move(commands)), index_(index) {
    ++g_object_count;
    for (auto* command : commands_) command->AddRef();
}
MenuEnumerator::~MenuEnumerator() {
    for (auto* command : commands_) command->Release();
    --g_object_count;
}
HRESULT MenuEnumerator::QueryInterface(REFIID iid, void** value) {
    if (!value) return E_POINTER;
    *value = nullptr;
    if (iid == IID_IUnknown || iid == __uuidof(IEnumExplorerCommand)) {
        *value = static_cast<IEnumExplorerCommand*>(this);
        AddRef();
        return S_OK;
    }
    return E_NOINTERFACE;
}
ULONG MenuEnumerator::Release() {
    const ULONG count = --references_;
    if (!count) delete this;
    return count;
}
HRESULT MenuEnumerator::Next(ULONG count, IExplorerCommand** commands, ULONG* fetched) {
    if (!commands || (count != 1 && !fetched)) return E_POINTER;
    ULONG copied = 0;
    while (copied < count && index_ < commands_.size()) {
        commands[copied] = commands_[index_++];
        commands[copied]->AddRef();
        ++copied;
    }
    if (fetched) *fetched = copied;
    return copied == count ? S_OK : S_FALSE;
}
HRESULT MenuEnumerator::Skip(ULONG count) {
    const size_t remaining = commands_.size() - index_;
    const ULONG skipped = static_cast<ULONG>(remaining < count ? remaining : count);
    index_ += skipped;
    return skipped == count ? S_OK : S_FALSE;
}
HRESULT MenuEnumerator::Clone(IEnumExplorerCommand** result) {
    if (!result) return E_POINTER;
    *result = new (std::nothrow) MenuEnumerator(commands_, index_);
    return *result ? S_OK : E_OUTOFMEMORY;
}

static MenuNode* LoadRoot() {
    auto* root = new (std::nothrow) MenuNode(L"Skill Magnet", L"", true);
    if (!root) return nullptr;
    const std::string data = ReadMenuManifest();
    size_t start = 0;
    bool header_seen = false;
    std::wstring first_pack;
    std::wstring first_label;
    std::wstring first_kind;
    bool multiple_packs = false;
    while (start < data.size()) {
        size_t end = data.find('\n', start);
        if (end == std::string::npos) end = data.size();
        std::string line = data.substr(start, end - start);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        start = end + 1;
        if (!header_seen) {
            header_seen = line == "skill-magnet-menu-v2";
            if (!header_seen) break;
            continue;
        }
        if (line.empty()) continue;
        const auto fields = SplitFields(line);
        if (fields.size() != 6 || fields[0].empty() || fields[1].empty() ||
            (fields[2] != L"package" && fields[2] != L"skill") || fields[3].empty() ||
            (fields[4] != L"Codex" && fields[4] != L"Claude") ||
            fields[5].find(kProjectMarker) == std::wstring::npos) continue;
        if (first_pack.empty()) {
            first_pack = fields[0];
            first_label = fields[1];
            first_kind = fields[2];
        } else if (fields[0] != first_pack) {
            multiple_packs = true;
        }
        // Windows 11's compact Explorer surface renders only the extension
        // root's immediate IExplorerCommand flyout reliably. Preserve the full
        // explicit selection in each leaf label instead of producing blank
        // recursive flyouts. The classic fallback retains the nested layout.
        const std::wstring leaf_title = multiple_packs
            ? L"Package: " + fields[1] + L" | Skill: " + fields[3] + L" | " + fields[4]
            : L"Skill: " + fields[3] + L" | " + fields[4];
        if (!root->AddLeaf(leaf_title, fields[5])) {
            root->Release();
            return nullptr;
        }
    }
    if (!first_pack.empty() && !multiple_packs) {
        root->SetTitle((first_kind == L"package" ? L"Package: " : L"Skill: ") + first_label);
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
BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

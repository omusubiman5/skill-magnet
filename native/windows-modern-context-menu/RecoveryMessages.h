#pragma once

#include <string>

namespace SkillMagnetRecovery {

inline constexpr wchar_t kCreateProcessFailurePrefix[] =
    L"Skill Magnetで選択した操作を開始できませんでした。\n\n"
    L"Windowsエラーコードと詳細:\n";

inline constexpr wchar_t kCreateProcessFailureRecovery[] =
    L"\n\n復旧手順:\n"
    L"1. この画面を開いたままCtrl+Cを押し、詳細とcommandをコピーします。\n"
    L"2. Windows TerminalでPowerShellタブを開きます。\n"
    L"3. 次の独立した修復・再登録commandを貼り付けて実行します。失敗した実行fileを"
    L"再利用せず、現在導入済みのPython 3.12を探します:\n";

inline constexpr wchar_t kRecoveryAfterCommand[] =
    L"\n4. このcommandは登録済みメニューに記録された正確な--config pathを保持します。\n"
    L"5. commandの完了後にcontext-menu-statusを実行し、"
    L"usable_installed_state=trueを確認してから、単一のSkill Magnetメニューを再実行します。\n"
    L"PythonまたはSkill Magnetがないと表示された場合は、setupに使った同じwheelから"
    L"Skill Magnet 0.5.9を再導入し、上のcommandを実行してください。\n\n"
    L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log\n"
    L"このfileをメモ帳で開き、event=create_process_failedを探してください。";

inline constexpr wchar_t kWaitFailurePrefix[] =
    L"Skill Magnetは選択した操作を開始しましたが、process状態を確認できませんでした。\n\n"
    L"Windowsエラーコードと詳細:\n";

inline constexpr wchar_t kWaitFailureRecovery[] =
    L"\n\n復旧手順:\n"
    L"1. この画面を開いたままCtrl+Cを押し、詳細とcommandをコピーします。\n"
    L"2. Windows TerminalでPowerShellタブを開き、次の正確な修復・再登録commandを"
    L"貼り付けて実行します:\n";

inline constexpr wchar_t kWaitAfterCommand[] =
    L"\n3. usable_installed_state=trueを確認し、停止したSkill Magnet画面だけを閉じてから"
    L"一度再実行します。\n\n"
    L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log\n"
    L"このfileをメモ帳で開き、event=child_wait_failedまたは"
    L"event=child_exit_read_failedを探してください。";

inline constexpr wchar_t kImmediateExitFailurePrefix[] =
    L"Skill Magnetは選択した操作を開始しましたが、画面が開く前に停止しました。\n\n"
    L"process終了コード: ";

inline constexpr wchar_t kImmediateExitFailureRecovery[] =
    L"\n\n復旧手順:\n"
    L"1. この画面を開いたままCtrl+Cを押し、詳細とcommandをコピーします。\n"
    L"2. Windows TerminalでPowerShellタブを開き、次の正確な修復・再登録commandを"
    L"貼り付けて実行します:\n";

inline constexpr wchar_t kImmediateExitAfterCommand[] =
    L"\n3. usable_installed_state=trueを確認してから、元の右クリック操作を再実行します。\n\n"
    L"診断ログ: %LOCALAPPDATA%\\SkillMagnet\\ContextMenu\\invoke.log\n"
    L"このfileをメモ帳で開き、上の終了コードを持つevent=child_process_failedを探してください。";

inline std::wstring PowerShellSingleQuoted(const std::wstring& value) {
    std::wstring quoted = L"'";
    for (const wchar_t character : value) {
        quoted.push_back(character);
        if (character == L'\'') quoted.push_back(L'\'');
    }
    quoted.push_back(L'\'');
    return quoted;
}

inline std::wstring InstalledPythonRepairCommand(const std::wstring& config_path) {
    std::wstring module_arguments = L" -I -m skill_magnet";
    if (config_path.empty()) {
        module_arguments += L" context-menu-status --platform windows";
    } else {
        module_arguments += L" --config " + PowerShellSingleQuoted(config_path) +
            L" install-context-menu --platform windows --confirm";
    }
    return L"$python = $null; "
        L"$py = Get-Command py.exe -ErrorAction SilentlyContinue; "
        L"if ($py) { "
        L"$candidate = & ($py.Source) -3.12 -c "
        L"'import sys; print(sys.executable)' 2>$null; "
        L"if ($LASTEXITCODE -eq 0 -and $candidate) { "
        L"$python = [string]($candidate | Select-Object -Last 1) } }; "
        L"if (-not $python) { "
        L"$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue; "
        L"if ($pythonCommand) { "
        L"$candidate = & ($pythonCommand.Source) -c "
        L"'import sys; assert sys.version_info >= (3, 12); print(sys.executable)' "
        L"2>$null; "
        L"if ($LASTEXITCODE -eq 0 -and $candidate) { "
        L"$python = [string]($candidate | Select-Object -Last 1) } } }; "
        L"if (-not $python) { "
        L"throw 'Python 3.12以降が導入されていません。' }; "
        L"& $python" + module_arguments;
}

inline const wchar_t* CreateProcessFailureAdvice(unsigned long error_code) {
    switch (error_code) {
        case 2:  // ERROR_FILE_NOT_FOUND
        case 3:  // ERROR_PATH_NOT_FOUND
            return L"登録済みメニューに記録されたPython実行fileが存在しません。"
                L"下の修復commandで現在のPythonを探します。見つからない場合は、先に"
                L"Python 3.12を修復または再導入してください。";
        case 5:    // ERROR_ACCESS_DENIED
        case 740:  // ERROR_ELEVATION_REQUIRED
        case 4551: // ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
            return L"Windowsのaccessまたはapplication-control policyが登録済み実行fileを"
                L"拒否しました。policyを無効にせず、信頼できるPython 3.12とSkill Magnet "
                L"wheelを修復または再導入してから、下の独立commandを実行してください。";
        case 193:  // ERROR_BAD_EXE_FORMAT
        case 216:  // ERROR_EXE_MACHINE_TYPE_MISMATCH
            return L"登録済みPython実行fileのarchitectureをこのWindowsでは実行できません。"
                L"正しいarchitectureのPython 3.12を修復または再導入してから、下の独立"
                L"commandを実行してください。";
        default:
            return L"Windowsは登録済みprocessを作成できませんでした。停止したSkill Magnet "
                L"processだけを閉じ、下の独立commandで登録済みメニューを修復してください。";
    }
}

inline std::wstring CreateProcessFailureMessage(
        unsigned long error_code, const std::wstring& windows_error,
        const std::wstring& independent_repair_command) {
    return std::wstring(kCreateProcessFailurePrefix) + windows_error +
        L"\n\n原因:\n" + CreateProcessFailureAdvice(error_code) +
        kCreateProcessFailureRecovery + independent_repair_command + kRecoveryAfterCommand;
}

inline std::wstring WaitFailureMessage(
        const std::wstring& windows_error, const std::wstring& repair_command) {
    return std::wstring(kWaitFailurePrefix) + windows_error + kWaitFailureRecovery +
        repair_command + kWaitAfterCommand;
}

inline std::wstring ImmediateExitFailureMessage(
        unsigned long exit_code, const std::wstring& repair_command) {
    return std::wstring(kImmediateExitFailurePrefix) + std::to_wstring(exit_code) +
        kImmediateExitFailureRecovery + repair_command + kImmediateExitAfterCommand;
}

}  // namespace SkillMagnetRecovery

param(
    [Parameter(Mandatory=$true)]
    [string]$Config,
    [Parameter(Mandatory=$true)]
    [string]$InvokeEvidence,
    [Parameter(Mandatory=$true)]
    [string]$FieldBundle
)

$ErrorActionPreference = "Stop"

function Assert-Field([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        ([BitConverter]::ToString($algorithm.ComputeHash($Bytes)) -replace "-", "").ToLowerInvariant()
    }
    finally { $algorithm.Dispose() }
}

function Get-Utf16Sha256([string]$Value) {
    Get-BytesSha256 ([Text.Encoding]::Unicode.GetBytes($Value))
}

function Get-Utf8Sha256([string]$Value) {
    Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($Value))
}

function Get-CanonicalStringArraySha256([object[]]$Values) {
    $normalized = @($Values | ForEach-Object { [string]$_ })
    $canonical = ConvertTo-Json -InputObject $normalized -Compress
    Get-Utf8Sha256 $canonical
}

function Get-FieldTargetSha256([string]$Path) {
    # Mirrors pathlib.Path.resolve + os.path.normpath/normcase in the installed
    # Windows UI without persisting the selected path in its recovery receipt.
    $normalized = [IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()
    Get-Utf8Sha256 $normalized
}

function Assert-FieldRegularPathBoundary(
    [string]$Path,
    [bool]$AllowMissingLeaf = $false
) {
    $full = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $full
    Assert-Field ($parent -and (Test-Path -LiteralPath $parent -PathType Container)) `
        "Field path parent does not exist: $parent"
    $parentItem = Get-Item -LiteralPath $parent -Force
    Assert-Field (
        ($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
    ) "Field path parent must not be a link, junction, or reparse point: $parent"
    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full -Force
        Assert-Field (
            -not $item.PSIsContainer -and
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        ) "Field path must not be a link, junction, or reparse point: $full"
    }
    else {
        Assert-Field $AllowMissingLeaf "Required field path is missing: $full"
    }
    $full
}

function Get-NormalizedTextFileSha256([string]$Path) {
    $text = [IO.File]::ReadAllText(
        $Path,
        [Text.UTF8Encoding]::new($false, $true)
    ).Replace("`r`n", "`n")
    Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($text))
}

function Get-NativeSourceManifest([string]$RepositoryRoot) {
    $nativeRoot = Join-Path $RepositoryRoot "native\windows-modern-context-menu"
    $inputs = @(
        "AppxManifest.xml", "ContractTest.cpp", "RecoveryMessages.h",
        "SkillMagnetCommand.cpp", "SkillMagnetCommand.def",
        "SkillMagnetIdentity.cpp", "SkillMagnetMenu.tsv", "build-package.ps1",
        "build.ps1", "certificate-state.ps1", "contract_test.py", "package.ps1"
    )
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $combined = [IO.MemoryStream]::new()
    $records = @()
    try {
        foreach ($relative in $inputs) {
            $raw = [IO.File]::ReadAllBytes((Join-Path $nativeRoot $relative))
            $offset = if (
                $raw.Length -ge 3 -and $raw[0] -eq 0xef -and
                $raw[1] -eq 0xbb -and $raw[2] -eq 0xbf
            ) { 3 } else { 0 }
            $text = $utf8.GetString($raw, $offset, $raw.Length - $offset)
            $normalized = [Text.UTF8Encoding]::new($false).GetBytes(
                $text.Replace("`r`n", "`n")
            )
            $name = [Text.UTF8Encoding]::new($false).GetBytes($relative)
            $combined.Write($name, 0, $name.Length)
            $combined.WriteByte(0)
            $combined.Write($normalized, 0, $normalized.Length)
            $combined.WriteByte(0)
            $records += [ordered]@{
                path = $relative
                normalized_size = $normalized.Length
                sha256 = Get-BytesSha256 $normalized
            }
        }
        [ordered]@{
            schema_version = 1
            contract = "skill-magnet-native-source-v1"
            source_tree_sha256 = Get-BytesSha256 $combined.ToArray()
            inputs = $records
        }
    }
    finally { $combined.Dispose() }
}

function New-ArtifactSnapshot(
    [string]$Source,
    [string]$FileName,
    [string]$Path
) {
    Assert-Field (Test-Path -LiteralPath $Path -PathType Leaf) `
        "Installed field artifact is missing: $FileName"
    $bytes = [IO.File]::ReadAllBytes($Path)
    [ordered]@{
        source = $Source
        file_name = $FileName
        size = $bytes.Length
        sha256 = Get-BytesSha256 $bytes
        bytes_base64 = [Convert]::ToBase64String($bytes)
    }
}

function Get-ZipEntryBytes($Archive, [string]$Name) {
    $matches = @($Archive.Entries | Where-Object {
        [string]$_.FullName -ceq $Name
    })
    Assert-Field ($matches.Count -eq 1) `
        "Signed MSIX entry is missing or ambiguous: $Name (count=$($matches.Count))"
    $entry = $matches[0]
    $stream = $entry.Open()
    $buffer = [IO.MemoryStream]::new()
    try {
        $stream.CopyTo($buffer)
        $buffer.ToArray()
    }
    finally {
        $buffer.Dispose()
        $stream.Dispose()
    }
}

function Get-UiaElementSnapshot($Element) {
    $rectangle = $Element.Current.BoundingRectangle
    $runtimeId = @($Element.GetRuntimeId())
    Assert-Field ($runtimeId.Count -ge 2) "UIAutomation element has no stable runtime id."
    [ordered]@{
        name = [string]$Element.Current.Name
        control_type = [string]$Element.Current.ControlType.ProgrammaticName
        automation_id = [string]$Element.Current.AutomationId
        class_name = [string]$Element.Current.ClassName
        framework_id = [string]$Element.Current.FrameworkId
        process_id = [int]$Element.Current.ProcessId
        native_window_handle = [int]$Element.Current.NativeWindowHandle
        is_enabled = [bool]$Element.Current.IsEnabled
        is_offscreen = [bool]$Element.Current.IsOffscreen
        bounding_rectangle = [ordered]@{
            left = [double]$rectangle.Left
            top = [double]$rectangle.Top
            width = [double]$rectangle.Width
            height = [double]$rectangle.Height
        }
        runtime_id = @($runtimeId | ForEach-Object { [int]$_ })
    }
}

function Add-UiaTranscriptEvent(
    [string]$Event,
    [string]$Source,
    [System.Collections.IDictionary]$Data
) {
    $script:UiaTranscriptSequence += 1
    $entry = [ordered]@{
        sequence = $script:UiaTranscriptSequence
        observed_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        session_id = $script:FieldSessionId
        event = $Event
        source = $Source
        data = $Data
    }
    $line = $entry | ConvertTo-Json -Depth 16 -Compress
    $null = $script:UiaTranscriptLines.Add($line)
}

function Get-NativeSequenceSha256($Sequence) {
    $bytes = [Text.Encoding]::Unicode.GetBytes(
        (($Sequence.lines -join "`r`n") + "`r`n")
    )
    Get-BytesSha256 $bytes
}

function Convert-FieldScalar($Value) {
    if ($Value -is [bool]) { return $Value.ToString().ToLowerInvariant() }
    if ($null -eq $Value) { return "" }
    [string]$Value
}

function Get-CanonicalJsonSha256($Value, [int]$Depth = 30) {
    $json = $Value | ConvertTo-Json -Depth $Depth -Compress
    Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($json))
}

function New-AttestationPayload([System.Collections.IDictionary]$Bundle) {
    $selected = @($Bundle.explorer_observations | Where-Object source -eq "selected_item")[0]
    $background = @($Bundle.explorer_observations | Where-Object source -eq "background_site")[0]
    $values = @(
        @("contract", "skill-magnet-windows-explorer-field-v5"),
        @("schema_version", $Bundle.schema_version),
        @("release_version", $Bundle.release_version),
        @("release_code_sha", $Bundle.release_code_sha),
        @("field_status", $Bundle.field_status),
        @("observed_at_utc", $Bundle.observed_at_utc),
        @("collector_sha256", $Bundle.collector_sha256),
        @("ui_receipts_sha256", (Get-CanonicalJsonSha256 $Bundle.ui_receipts)),
        @("python_runtime.module_version", $Bundle.python_runtime.module_version),
        @("python_runtime.distribution_version", $Bundle.python_runtime.distribution_version),
        @("python_runtime.distribution_name", $Bundle.python_runtime.distribution_name),
        @("python_runtime.executable_path_sha256", $Bundle.python_runtime.executable_path_sha256),
        @("python_runtime.module_path_sha256", $Bundle.python_runtime.module_path_sha256),
        @("python_runtime.distribution_module_path_sha256", $Bundle.python_runtime.distribution_module_path_sha256),
        @("python_runtime.payload_sha256", $Bundle.python_runtime.payload_sha256),
        @("package.name", $Bundle.package.name),
        @("package.version", $Bundle.package.version),
        @("package.architecture", $Bundle.package.architecture),
        @("package.publisher", $Bundle.package.publisher),
        @("package.package_full_name", $Bundle.package.package_full_name),
        @("package.same_name_package_count", $Bundle.package.same_name_package_count),
        @("package.expected_identity_match_count", $Bundle.package.expected_identity_match_count),
        @("package.unexpected_same_name_package_count", $Bundle.package.unexpected_same_name_package_count),
        @("package.usable_installed_state", $Bundle.package.usable_installed_state),
        @("package.menu_contract_matches_config", $Bundle.package.menu_contract_matches_config),
        @("package.command_target_signature_valid", $Bundle.package.command_target_signature_valid),
        @("native_source_binding.contract", $Bundle.native_source_binding.contract),
        @("native_source_binding.source_tree_sha256", $Bundle.native_source_binding.source_tree_sha256),
        @("native_source_binding.package_manifest_source_tree_sha256", $Bundle.native_source_binding.package_manifest_source_tree_sha256),
        @("native_source_binding.external_manifest_source_tree_sha256", $Bundle.native_source_binding.external_manifest_source_tree_sha256),
        @("native_source_binding.package_dll_export_source_tree_sha256", $Bundle.native_source_binding.package_dll_export_source_tree_sha256),
        @("native_source_binding.external_dll_export_source_tree_sha256", $Bundle.native_source_binding.external_dll_export_source_tree_sha256),
        @("native_source_binding.package_dll_embedded_binding_count", $Bundle.native_source_binding.package_dll_embedded_binding_count),
        @("native_source_binding.external_dll_embedded_binding_count", $Bundle.native_source_binding.external_dll_embedded_binding_count),
        @("native_source_binding.package_external_artifacts_equal", $Bundle.native_source_binding.package_external_artifacts_equal),
        @("native_source_binding.signed_msix_payload_matches_package", $Bundle.native_source_binding.signed_msix_payload_matches_package),
        @("native_source_binding.isolated_contract_probe_passed", $Bundle.native_source_binding.isolated_contract_probe_passed),
        @("native_source_binding.isolated_contract_probe_mode", $Bundle.native_source_binding.isolated_contract_probe_mode),
        @("native_source_binding.status_native_source_tree_sha256", $Bundle.native_source_binding.status_native_source_tree_sha256),
        @("native_source_binding.status_native_source_manifest_valid", $Bundle.native_source_binding.status_native_source_manifest_valid),
        @("native_source_binding.status_native_artifact_hashes_valid", $Bundle.native_source_binding.status_native_artifact_hashes_valid),
        @("native_source_binding.status_dll_native_source_binding_valid", $Bundle.native_source_binding.status_dll_native_source_binding_valid),
        @("native_source_binding.status_native_build_binding_valid", $Bundle.native_source_binding.status_native_build_binding_valid),
        @("selected_item.invocation_id", $selected.invocation_id),
        @("selected_item.project_sha256", $selected.project_sha256),
        @("selected_item.selection_choice_values_sha256", $selected.selection_choice_values_sha256),
        @("selected_item.selected_choice_value_sha256", $selected.selected_choice_value_sha256),
        @("selected_item.library_manager_button_text_sha256", $selected.library_manager_button_text_sha256),
        @("selected_item.register_button_text_sha256", $selected.register_button_text_sha256),
        @("background_site.invocation_id", $background.invocation_id),
        @("background_site.project_sha256", $background.project_sha256),
        @("background_site.selection_choice_values_sha256", $background.selection_choice_values_sha256),
        @("background_site.selected_choice_value_sha256", $background.selected_choice_value_sha256),
        @("background_site.library_manager_button_text_sha256", $background.library_manager_button_text_sha256),
        @("background_site.register_button_text_sha256", $background.register_button_text_sha256),
        @("selector.choice_map_sha256", $Bundle.selector_contract.choice_map_sha256),
        @("selector.ordered_label_sha256", $Bundle.selector_contract.ordered_label_sha256),
        @("selector.choice_count", $Bundle.selector_contract.choice_count),
        @("selector.selected_label_sha256", $Bundle.selector_contract.selected_label_sha256),
        @("selector.exact_selector_combo_count", $Bundle.selector_contract.exact_selector_combo_count),
        @("library_manager.configured_remote_sha256", $Bundle.library_manager_observation.configured_remote_sha256),
        @("library_manager.configured_remote_visible", $Bundle.library_manager_observation.configured_remote_visible),
        @("library_manager.create_button_text_sha256", $Bundle.library_manager_observation.create_button_text_sha256),
        @("library_manager.update_button_text_sha256", $Bundle.library_manager_observation.update_button_text_sha256),
        @("library_manager.delete_button_text_sha256", $Bundle.library_manager_observation.delete_button_text_sha256),
        @("library_manager.reload_button_text_sha256", $Bundle.library_manager_observation.reload_button_text_sha256),
        @("library_manager.same_folder_repeat_focused_existing_manager", $Bundle.library_manager_observation.same_folder_repeat_focused_existing_manager),
        @("library_manager.different_folder_actionable_recovery_visible", $Bundle.library_manager_observation.different_folder_actionable_recovery_visible),
        @("library_manager.no_persistent_mutation", $Bundle.library_manager_observation.no_persistent_mutation),
        @("registration.selected_path_sha256", $Bundle.registration_recovery_observation.selected_path_sha256),
        @("registration.selected_path_visible", $Bundle.registration_recovery_observation.selected_path_visible),
        @("registration.missing_skill_cause_visible", $Bundle.registration_recovery_observation.missing_skill_cause_visible),
        @("registration.actionable_recovery_visible", $Bundle.registration_recovery_observation.actionable_recovery_visible),
        @("registration.no_persistent_mutation", $Bundle.registration_recovery_observation.no_persistent_mutation),
        @("runtime_skill.clicked_path_sha256", $Bundle.runtime_skill_observation.clicked_path_sha256),
        @("runtime_skill.runtime_path_hidden_as_workspace", $Bundle.runtime_skill_observation.runtime_path_hidden_as_workspace),
        @("runtime_skill.projectless_semantics_visible", $Bundle.runtime_skill_observation.projectless_semantics_visible),
        @("runtime_skill.skill_content_sha256", $Bundle.runtime_skill_observation.skill_content_sha256),
        @("runtime_skill.read_only", $Bundle.runtime_skill_observation.read_only),
        @("recovery.same_folder_repeat_focused_existing_window", $Bundle.recovery_observations.same_folder_repeat_focused_existing_window),
        @("recovery.same_folder_repeat_gui_count", $Bundle.recovery_observations.same_folder_repeat_gui_count),
        @("recovery.different_folder_busy_message_visible", $Bundle.recovery_observations.different_folder_busy_message_visible),
        @("recovery.different_folder_actionable_recovery_visible", $Bundle.recovery_observations.different_folder_actionable_recovery_visible),
        @("recovery.closed_window_relaunch_succeeded", $Bundle.recovery_observations.closed_window_relaunch_succeeded),
        @("hashes.appx_manifest_sha256", $Bundle.hashes.appx_manifest_sha256),
        @("hashes.menu_manifest_sha256", $Bundle.hashes.menu_manifest_sha256),
        @("hashes.dll_sha256", $Bundle.hashes.dll_sha256),
        @("hashes.identity_sha256", $Bundle.hashes.identity_sha256),
        @("hashes.native_source_manifest_sha256", $Bundle.hashes.native_source_manifest_sha256),
        @("hashes.external_dll_sha256", $Bundle.hashes.external_dll_sha256),
        @("hashes.external_identity_sha256", $Bundle.hashes.external_identity_sha256),
        @("hashes.external_native_source_manifest_sha256", $Bundle.hashes.external_native_source_manifest_sha256),
        @("hashes.signed_msix_sha256", $Bundle.hashes.signed_msix_sha256),
        @("hashes.contract_probe_output_sha256", $Bundle.hashes.contract_probe_output_sha256),
        @("hashes.config_sha256", $Bundle.hashes.config_sha256),
        @("hashes.config_path_sha256", $Bundle.hashes.config_path_sha256),
        @("hashes.invoke_log_sha256", $Bundle.hashes.invoke_log_sha256),
        @("hashes.uia_transcript_sha256", $Bundle.hashes.uia_transcript_sha256)
    )
    $lines = @($values | ForEach-Object {
        [string]$_[0] + "=" + (Convert-FieldScalar $_[1])
    })
    [Text.UTF8Encoding]::new($false).GetBytes(($lines -join "`n") + "`n")
}

function New-DetachedAttestation([byte[]]$Payload, [string]$DllPath) {
    Add-Type -AssemblyName System.Security
    $dllSignature = Get-AuthenticodeSignature -LiteralPath $DllPath
    Assert-Field ($dllSignature.Status.ToString() -eq "Valid") `
        "Installed command DLL Authenticode signature is not valid."
    Assert-Field ($null -ne $dllSignature.SignerCertificate) `
        "Installed command DLL has no signer certificate."
    $thumbprint = $dllSignature.SignerCertificate.Thumbprint
    $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $thumbprint)
    Assert-Field ($certificate.HasPrivateKey) `
        "Installed command DLL signer private key is unavailable for field attestation."
    $content = [Security.Cryptography.Pkcs.ContentInfo]::new($Payload)
    $signed = [Security.Cryptography.Pkcs.SignedCms]::new($content, $true)
    $signer = [Security.Cryptography.Pkcs.CmsSigner]::new($certificate)
    $signer.DigestAlgorithm = [Security.Cryptography.Oid]::new(
        "2.16.840.1.101.3.4.2.1"
    )
    $signer.IncludeOption = [Security.Cryptography.X509Certificates.X509IncludeOption]::EndCertOnly
    $signed.ComputeSignature($signer)
    [ordered]@{
        algorithm = "sha256-rsa-cms-detached"
        signer_thumbprint = $thumbprint.ToLowerInvariant()
        signed_payload_sha256 = Get-BytesSha256 $Payload
        signature_base64 = [Convert]::ToBase64String($signed.Encode())
    }
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -ReferencedAssemblies @(
    "UIAutomationClient", "UIAutomationTypes", "WindowsBase"
) -TypeDefinition @"
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Automation;
public static class SkillMagnetFieldInput {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hWnd, int command);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(
        uint sourceThreadId, uint targetThreadId, bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(
        IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(
        IntPtr hWnd, out RECT rectangle);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(
        IntPtr hWnd, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] public static extern void mouse_event(
        uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextLength(IntPtr hWnd);
    public static string WindowText(IntPtr hWnd) {
        int length = GetWindowTextLength(hWnd);
        StringBuilder text = new StringBuilder(length + 1);
        GetWindowText(hWnd, text, text.Capacity);
        return text.ToString();
    }
    public static bool FocusWindow(IntPtr hWnd) {
        if (!IsWindow(hWnd)) return false;
        IntPtr foreground = GetForegroundWindow();
        uint ignored;
        uint foregroundThread = foreground == IntPtr.Zero
            ? 0 : GetWindowThreadProcessId(foreground, out ignored);
        uint targetThread = GetWindowThreadProcessId(hWnd, out ignored);
        uint currentThread = GetCurrentThreadId();
        bool attachedForeground = foregroundThread != 0 && foregroundThread != currentThread
            && AttachThreadInput(currentThread, foregroundThread, true);
        bool attachedTarget = targetThread != 0 && targetThread != currentThread
            && targetThread != foregroundThread && AttachThreadInput(currentThread, targetThread, true);
        try {
            ShowWindow(hWnd, 9); // SW_RESTORE
            BringWindowToTop(hWnd);
            SetForegroundWindow(hWnd);
            return GetForegroundWindow() == hWnd;
        }
        finally {
            if (attachedTarget) AttachThreadInput(currentThread, targetThread, false);
            if (attachedForeground) AttachThreadInput(currentThread, foregroundThread, false);
        }
    }
    private static string Sha256(byte[] value) {
        using (SHA256 algorithm = SHA256.Create()) {
            byte[] digest = algorithm.ComputeHash(value);
            StringBuilder result = new StringBuilder(64);
            foreach (byte item in digest) result.Append(item.ToString("x2"));
            return result.ToString();
        }
    }
    private static string Sha256(string value) {
        return Sha256(new UTF8Encoding(false, true).GetBytes(value));
    }
    private static bool FixedToken(string value, int length) {
        if (value == null || value.Length != length) return false;
        foreach (char item in value) {
            if (!((item >= '0' && item <= '9') || (item >= 'a' && item <= 'f'))) return false;
        }
        return true;
    }
    private static byte[] ReadPinnedReceipt(string path, out FileStream stream) {
        stream = null;
        if (String.IsNullOrEmpty(path)) return null;
        stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (stream.Length <= 0 || stream.Length > 262144) return null;
        byte[] bytes = new byte[(int)stream.Length];
        int offset = 0;
        while (offset < bytes.Length) {
            int read = stream.Read(bytes, offset, bytes.Length - offset);
            if (read <= 0) return null;
            offset += read;
        }
        return bytes;
    }
    private static bool ReceiptMatches(
        byte[] bytes, string expectedReceiptSha256, string processInstanceId,
        string generation, long revision, string semanticId,
        string semanticNameSha256) {
        if (bytes == null || !FixedToken(expectedReceiptSha256, 64) ||
            !FixedToken(processInstanceId, 32) || !FixedToken(generation, 32) ||
            revision <= 0 || String.IsNullOrEmpty(semanticId) ||
            !FixedToken(semanticNameSha256, 64) ||
            !String.Equals(Sha256(bytes), expectedReceiptSha256, StringComparison.Ordinal)) {
            return false;
        }
        string json;
        try { json = new UTF8Encoding(false, true).GetString(bytes); }
        catch { return false; }
        string space = @"\s*";
        return Regex.IsMatch(json, "\\\"process_instance_id\\\"" + space + ":" +
                space + "\\\"" + Regex.Escape(processInstanceId) + "\\\"") &&
            Regex.IsMatch(json, "\\\"generation\\\"" + space + ":" + space +
                "\\\"" + Regex.Escape(generation) + "\\\"") &&
            Regex.IsMatch(json, "\\\"revision\\\"" + space + ":" + space +
                revision.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                @"(?:\s*[,}])") &&
            Regex.IsMatch(json, "\\\"id\\\"" + space + ":" + space + "\\\"" +
                Regex.Escape(semanticId) + "\\\"") &&
            Regex.IsMatch(json, "\\\"text_sha256\\\"" + space + ":" + space +
                "\\\"" + Regex.Escape(semanticNameSha256) + "\\\"");
    }
    private static bool ProcessMatches(
        uint expectedProcessId, string expectedExecutablePath,
        long expectedStartTimeUtcTicks) {
        try {
            Process process = Process.GetProcessById((int)expectedProcessId);
            return process.Id == (int)expectedProcessId &&
                process.StartTime.ToUniversalTime().Ticks == expectedStartTimeUtcTicks &&
                String.Equals(
                    Path.GetFullPath(process.MainModule.FileName),
                    Path.GetFullPath(expectedExecutablePath),
                    StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }
    public static Action TestAfterInitialValidation;
    public static bool CheckedClickCurrent(
        int x, int y, IntPtr widget, IntPtr root, uint expectedProcessId,
        string expectedExecutablePath, long expectedStartTimeUtcTicks,
        bool requireUiaHandle, string expectedUiaNameSha256,
        string receiptPath, string expectedReceiptSha256,
        string expectedProcessInstanceId, string expectedGeneration,
        long expectedRevision, string expectedSemanticId, bool rightClick) {
        FileStream initialReceipt = null;
        FileStream finalReceipt = null;
        try {
        byte[] initialReceiptBytes = ReadPinnedReceipt(receiptPath, out initialReceipt);
        POINT cursor;
        if (!GetCursorPos(out cursor) || cursor.X != x || cursor.Y != y) return false;
        if (GetForegroundWindow() != root) return false;
        POINT point = new POINT { X = x, Y = y };
        IntPtr hit = WindowFromPoint(point);
        if (hit != widget || GetAncestor(hit, 2) != root) return false;
        uint processId;
        GetWindowThreadProcessId(hit, out processId);
        if (processId != expectedProcessId) return false;
        if (!ProcessMatches(expectedProcessId, expectedExecutablePath,
            expectedStartTimeUtcTicks)) return false;
        AutomationElement initialUia;
        try { initialUia = AutomationElement.FromPoint(new System.Windows.Point(x, y)); }
        catch { return false; }
        if (initialUia == null || initialUia.Current.ProcessId != (int)expectedProcessId ||
            !initialUia.Current.IsEnabled || initialUia.Current.IsOffscreen ||
            (requireUiaHandle && initialUia.Current.NativeWindowHandle != widget.ToInt32()) ||
            !String.Equals(Sha256(initialUia.Current.Name), expectedUiaNameSha256,
                StringComparison.Ordinal)) return false;
        if (!String.IsNullOrEmpty(receiptPath) && !ReceiptMatches(
            initialReceiptBytes, expectedReceiptSha256, expectedProcessInstanceId,
            expectedGeneration, expectedRevision, expectedSemanticId,
            expectedUiaNameSha256)) return false;
        if (initialReceipt != null) { initialReceipt.Dispose(); initialReceipt = null; }
        Action fault = TestAfterInitialValidation;
        if (fault != null) fault();
        uint down = rightClick ? 0x0008u : 0x0002u;
        uint up = rightClick ? 0x0010u : 0x0004u;
        byte[] finalReceiptBytes = ReadPinnedReceipt(receiptPath, out finalReceipt);
        AutomationElement finalUia;
        try { finalUia = AutomationElement.FromPoint(new System.Windows.Point(x, y)); }
        catch { return false; }
        POINT finalCursor;
        IntPtr finalForeground = GetForegroundWindow();
        IntPtr finalHit = WindowFromPoint(point);
        uint finalProcessId;
        GetWindowThreadProcessId(finalHit, out finalProcessId);
        // This is the final fail-closed boundary.  The next statements are the
        // mouse send itself; no sleep, callback, UIA lookup, receipt read, or
        // HWND lookup may occur between this complete identity check and send.
        if (!GetCursorPos(out finalCursor) || finalCursor.X != x || finalCursor.Y != y ||
            finalForeground != root || finalHit != widget ||
            GetAncestor(finalHit, 2) != root || finalProcessId != expectedProcessId ||
            !ProcessMatches(expectedProcessId, expectedExecutablePath,
                expectedStartTimeUtcTicks) || finalUia == null ||
            finalUia.Current.ProcessId != (int)expectedProcessId ||
            !finalUia.Current.IsEnabled || finalUia.Current.IsOffscreen ||
            (requireUiaHandle && finalUia.Current.NativeWindowHandle != widget.ToInt32()) ||
            !String.Equals(Sha256(finalUia.Current.Name), expectedUiaNameSha256,
                StringComparison.Ordinal) ||
            (!String.IsNullOrEmpty(receiptPath) && !ReceiptMatches(
                finalReceiptBytes, expectedReceiptSha256, expectedProcessInstanceId,
                expectedGeneration, expectedRevision, expectedSemanticId,
                expectedUiaNameSha256))) return false;
        mouse_event(down, 0, 0, 0, UIntPtr.Zero);
        mouse_event(up, 0, 0, 0, UIntPtr.Zero);
        return true;
        }
        catch { return false; }
        finally {
            if (initialReceipt != null) initialReceipt.Dispose();
            if (finalReceipt != null) finalReceipt.Dispose();
        }
    }
}
"@

function Get-VisibleNamedElements([string]$Name, [int]$ProcessId = 0) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, $Name
    )
    $matches = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $condition
    )
    @($matches | Where-Object {
        try {
            -not $_.Current.IsOffscreen -and (
                $ProcessId -le 0 -or [int]$_.Current.ProcessId -eq $ProcessId
            )
        } catch { $false }
    })
}

function New-HashedArtifactSnapshot(
    [string]$Source,
    [string]$FileName,
    [string]$Path
) {
    Assert-Field (Test-Path -LiteralPath $Path -PathType Leaf) `
        "Installed field artifact is missing: $FileName"
    $bytes = [IO.File]::ReadAllBytes($Path)
    [ordered]@{
        source = $Source
        file_name = $FileName
        size = $bytes.Length
        sha256 = Get-BytesSha256 $bytes
    }
}

function Get-UiaRuntimeKey($Element) {
    try { (@($Element.GetRuntimeId()) | ForEach-Object { [string]$_ }) -join "." }
    catch { "" }
}

function Wait-VisibleNamedElement(
    [string]$Name,
    [int]$ProcessId = 0,
    [int]$Seconds = 12
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $matches = @(Get-VisibleNamedElements $Name $ProcessId)
        if ($matches.Count -gt 0) { return $matches[0] }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "UI Automation element did not appear: $Name"
}

function Get-Pattern($Element, $Pattern) {
    $value = $null
    if ($Element.TryGetCurrentPattern($Pattern, [ref]$value)) { return $value }
    return $null
}

function Open-ExplorerFolder([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    Start-Process explorer.exe -ArgumentList @("/n,", $resolved) | Out-Null
    $shell = New-Object -ComObject Shell.Application
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        foreach ($window in @($shell.Windows())) {
            try {
                if (([Uri]$window.LocationURL).LocalPath.TrimEnd('\') -eq $resolved.TrimEnd('\')) {
                    return $window
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Explorer did not open the field-test folder."
}

function Get-ExplorerElement($Window) {
    $handle = [IntPtr]([int64]$Window.HWND)
    Assert-Field ([SkillMagnetFieldInput]::FocusWindow($handle)) `
        "Could not foreground the Explorer field-test window."
    Start-Sleep -Milliseconds 250
    [System.Windows.Automation.AutomationElement]::FromHandle($handle)
}

function Invoke-CheckedExplorerPhysicalClick(
    $Window,
    [int]$X,
    [int]$Y,
    [bool]$RightClick,
    $ExpectedElement = $null
) {
    $rootHandle = [IntPtr]([int64]$Window.HWND)
    $rootPid = [uint32]0
    $null = [SkillMagnetFieldInput]::GetWindowThreadProcessId($rootHandle, [ref]$rootPid)
    $identity = Get-FieldProcessIdentity ([int]$rootPid)
    Assert-Field ($null -ne $identity -and (Test-FieldProcessIdentity $identity)) `
        "Explorer process identity is unavailable before physical input."
    Assert-Field ([SkillMagnetFieldInput]::FocusWindow($rootHandle)) `
        "Could not foreground the exact Explorer window before physical input."
    Start-Sleep -Milliseconds 100
    $point = [SkillMagnetFieldInput+POINT]::new()
    $point.X = $X
    $point.Y = $Y
    $firstHwnd = [SkillMagnetFieldInput]::WindowFromPoint($point)
    $firstUia = [System.Windows.Automation.AutomationElement]::FromPoint(
        [System.Windows.Point]::new([double]$X, [double]$Y)
    )
    Assert-Field (
        $firstHwnd -ne [IntPtr]::Zero -and $null -ne $firstUia -and
        [SkillMagnetFieldInput]::GetAncestor($firstHwnd, 2) -eq $rootHandle -and
        [int]$firstUia.Current.ProcessId -eq [int]$rootPid
    ) "Explorer click point is not bound to the expected HWND/PID/root."
    if ($null -ne $ExpectedElement) {
        Assert-Field (
            (Get-UiaRuntimeKey $firstUia) -ceq (Get-UiaRuntimeKey $ExpectedElement) -and
            (Get-Utf8Sha256 ([string]$firstUia.Current.Name)) -ceq
                (Get-Utf8Sha256 ([string]$ExpectedElement.Current.Name))
        ) "Explorer click point does not hit the exact expected UIAutomation element."
    }
    $runtimeKey = Get-UiaRuntimeKey $firstUia
    $nameSha256 = Get-Utf8Sha256 ([string]$firstUia.Current.Name)
    Assert-Field ([SkillMagnetFieldInput]::SetCursorPos($X, $Y)) `
        "Could not move the cursor to the verified Explorer target."
    Start-Sleep -Milliseconds 40
    $finalHwnd = [SkillMagnetFieldInput]::WindowFromPoint($point)
    $finalUia = [System.Windows.Automation.AutomationElement]::FromPoint(
        [System.Windows.Point]::new([double]$X, [double]$Y)
    )
    Assert-Field (
        $null -ne $finalUia -and
        [SkillMagnetFieldInput]::GetForegroundWindow() -eq $rootHandle -and
        (Test-FieldProcessIdentity $identity) -and
        $finalHwnd -eq $firstHwnd -and
        [SkillMagnetFieldInput]::GetAncestor($finalHwnd, 2) -eq $rootHandle -and
        [int]$finalUia.Current.ProcessId -eq [int]$rootPid -and
        (Get-UiaRuntimeKey $finalUia) -ceq $runtimeKey -and
        (Get-Utf8Sha256 ([string]$finalUia.Current.Name)) -ceq $nameSha256
    ) "Explorer HWND/PID/process/UIA target changed; no mouse input was sent."
    Assert-Field ([SkillMagnetFieldInput]::CheckedClickCurrent(
        $X, $Y, $firstHwnd, $rootHandle, $rootPid,
        [string]$identity.executable_path, [long]$identity.start_time_utc_ticks,
        $false, $nameSha256, "", "", "", "", [long]0, "", $RightClick
    )) "Explorer target changed at the final input boundary; no mouse input was sent."
}

function Open-ExplorerContextMenu($Window, [string]$SelectedName = "") {
    $explorer = Get-ExplorerElement $Window
    $preExistingRootKeys = @{}
    foreach ($root in @(Get-VisibleNamedElements "Skill Magnet" | Where-Object {
        try {
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::MenuItem
        } catch { $false }
    })) {
        $key = Get-UiaRuntimeKey $root
        if ($key) { $preExistingRootKeys[$key] = $true }
    }
    if ($SelectedName) {
        $condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty, $SelectedName
        )
        $items = $explorer.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants, $condition
        )
        $candidates = @($items | Where-Object {
            try {
                $rectangle = $_.Current.BoundingRectangle
                -not $_.Current.IsOffscreen -and $rectangle.Width -gt 20 -and $rectangle.Height -gt 10
            } catch { $false }
        } | Sort-Object { $_.Current.BoundingRectangle.Width } -Descending)
        Assert-Field ($candidates.Count -gt 0) "Selected folder was not visible in Explorer."
        $rectangle = $candidates[0].Current.BoundingRectangle
        $x = [int]($rectangle.Left + ($rectangle.Width / 2))
        $y = [int]($rectangle.Top + ($rectangle.Height / 2))
        Invoke-CheckedExplorerPhysicalClick $Window $x $y $false $candidates[0]
        Start-Sleep -Milliseconds 100
        Invoke-CheckedExplorerPhysicalClick $Window $x $y $true
    }
    else {
        $rectangle = $explorer.Current.BoundingRectangle
        $x = [int]($rectangle.Left + ($rectangle.Width * 0.76))
        $y = [int]($rectangle.Top + ($rectangle.Height * 0.72))
        Invoke-CheckedExplorerPhysicalClick $Window $x $y $true
    }
    Start-Sleep -Milliseconds 300
    [ordered]@{
        pre_existing_root_keys = $preExistingRootKeys
        click_x = $x
        click_y = $y
    }
}

function Invoke-VisibleSkillMagnetRoot(
    $Window,
    [string]$SelectedName = "",
    [string]$TranscriptSource = ""
) {
    $openedMenu = Open-ExplorerContextMenu $Window $SelectedName
    $rootDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $rootByRuntime = @{}
        foreach ($root in @(Get-VisibleNamedElements "Skill Magnet") | Where-Object {
            try {
                $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::MenuItem
            } catch { $false }
        }) {
            $key = Get-UiaRuntimeKey $root
            if ($key -and -not $rootByRuntime.ContainsKey($key)) {
                $rootByRuntime[$key] = $root
            }
        }
        $roots = @($rootByRuntime.GetEnumerator() | Where-Object {
            -not $openedMenu.pre_existing_root_keys.ContainsKey([string]$_.Key)
        } | ForEach-Object { $_.Value })
        if ($roots.Count -gt 0) { break }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $rootDeadline)
    $diagnostic = @($rootByRuntime.Keys | Sort-Object) -join ","
    Assert-Field ($roots.Count -eq 1) `
        ("Explorer must expose exactly one newly visible Skill Magnet root; " +
         "observed $($roots.Count); all visible runtime ids: $diagnostic")
    $invoke = Get-Pattern $roots[0] ([System.Windows.Automation.InvokePattern]::Pattern)
    $expand = Get-Pattern $roots[0] ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
    Assert-Field ($null -ne $invoke) "Skill Magnet root has no InvokePattern."
    Assert-Field ($null -eq $expand) "Skill Magnet root unexpectedly exposes a submenu."
    $rootSnapshot = Get-UiaElementSnapshot $roots[0]
    if ($TranscriptSource) {
        Add-UiaTranscriptEvent "context_menu_root_observed" $TranscriptSource ([ordered]@{
            element = $rootSnapshot
            root_visible_count = $roots.Count
            invoke_pattern_available = ($null -ne $invoke)
            expand_collapse_pattern_available = ($null -ne $expand)
            submenu_item_count = 0
        })
    }
    $invoke.Invoke()
    if ($TranscriptSource) {
        Add-UiaTranscriptEvent "root_invoke_dispatched" $TranscriptSource ([ordered]@{
            runtime_id = @($rootSnapshot.runtime_id)
        })
    }
    @{
        element = $rootSnapshot
        root_visible_count = $roots.Count
        invoke_pattern_available = ($null -ne $invoke)
        expand_collapse_pattern_available = ($null -ne $expand)
        submenu_item_count = 0
    }
}

function Test-ExactStringSequence([object[]]$Actual, [object[]]$Expected) {
    $left = @($Actual | ForEach-Object { [string]$_ })
    $right = @($Expected | ForEach-Object { [string]$_ })
    if ($left.Count -ne $right.Count) { return $false }
    for ($index = 0; $index -lt $left.Count; $index += 1) {
        if ($left[$index] -cne $right[$index]) { return $false }
    }
    return $true
}

function Get-SelectionChoiceContract($Surface, [object[]]$ExpectedChoices) {
    $expectedLabels = @($ExpectedChoices | ForEach-Object { [string]$_.label })
    $selection = Get-FieldUiSurfaceWidget $Surface "selection_choice" "combobox"
    Assert-Field ([bool]$selection.viewable) `
        "The receipt-bound selection control is not visible."
    $expectedValuesSha256 = Get-CanonicalStringArraySha256 $expectedLabels
    Assert-Field (
        [string]$selection.values_sha256 -ceq $expectedValuesSha256
    ) "Receipt-bound selection choices do not match the configured ordered choice digest."
    $selectedLabel = if ($expectedLabels.Count -gt 0) { $expectedLabels[0] } else { "" }
    Assert-Field (
        [string]$selection.value_sha256 -ceq (Get-Utf8Sha256 $selectedLabel)
    ) "Receipt-bound selected choice does not match the configured default choice digest."
    [ordered]@{
        configured_choices = @($ExpectedChoices)
        labels = $expectedLabels
        values_sha256 = $expectedValuesSha256
        selected_value_sha256 = Get-Utf8Sha256 $selectedLabel
        exact_match_count = 1
        combo_box_count = 1
    }
}

function Assert-NoRawReceiptDisplayValues($Value, [string]$Path = "owner") {
    if ($null -eq $Value) { return }
    if ($Value -is [string] -or $Value -is [ValueType]) { return }
    if ($Value -is [System.Collections.IEnumerable] -and
        -not ($Value -is [System.Collections.IDictionary]) -and
        -not ($Value -is [pscustomobject])) {
        $index = 0
        foreach ($item in $Value) {
            Assert-NoRawReceiptDisplayValues $item "$Path[$index]"
            $index += 1
        }
        return
    }
    $properties = if ($Value -is [System.Collections.IDictionary]) {
        @($Value.Keys | ForEach-Object {
            [pscustomobject]@{ Name = [string]$_; Value = $Value[$_] }
        })
    } else { @($Value.PSObject.Properties) }
    foreach ($property in $properties) {
        $propertyName = [string]$property.Name
        Assert-Field (
            @("text", "value", "values") -cnotcontains $propertyName -and
            $propertyName -notmatch '_(?:length|count|present)$'
        ) "Receipt contains forbidden raw/metadata key '$propertyName' at $Path."
        Assert-NoRawReceiptDisplayValues $property.Value "$Path.$($property.Name)"
    }
}

function Get-ButtonCount($Gui, [string]$Name) {
    $nameCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, $Name
    )
    $typeCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )
    $condition = New-Object System.Windows.Automation.AndCondition(
        $nameCondition, $typeCondition
    )
    @($Gui.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)).Count
}

function Inspect-UnifiedGui(
    [string]$ProjectPath,
    [object[]]$ExpectedChoices,
    [int]$ExpectedProcessId,
    [string]$TranscriptSource = "",
    [bool]$ExpectProjectless = $false
) {
    $gui = Wait-VisibleWindowByPrefix "Skill Magnet — 実行確認" $ExpectedProcessId
    $receipt = Wait-FieldUiSurface $ExpectedProcessId "context_selection" $gui
    $surface = $receipt.surface
    $projectWidget = Get-FieldUiSurfaceWidget $surface "project" "label"
    $projectBound = [string]$receipt.owner.target_sha256 -ceq `
        (Get-FieldTargetSha256 $ProjectPath)
    $expectedProjectText = if ($ExpectProjectless) {
        "作業対象フォルダー: 指定なし（デスクトップアプリが新規タスク用領域を自動作成）"
    } else { "作業対象フォルダー: （選択済み）" }
    $projectSemanticVisible = (
        [string]$projectWidget.text_sha256 -ceq (Get-Utf8Sha256 $expectedProjectText)
    )
    $selectionContract = Get-SelectionChoiceContract $surface $ExpectedChoices
    $managerButton = Get-FieldUiSurfaceWidget $surface "library_manager" "button"
    $registerButton = Get-FieldUiSurfaceWidget $surface "register_selected" "button"
    $observation = @{
        element = $gui
        ui_surface = $surface
        ui_owner_receipt = $receipt.owner
        ui_surface_generation = [string]$receipt.owner.generation
        ui_surface_sha256 = [string]$receipt.receipt_sha256
        selection_choice_values_sha256 = [string]$selectionContract.values_sha256
        selected_choice_value_sha256 = [string]$selectionContract.selected_value_sha256
        library_manager_button_text_sha256 = Get-Utf8Sha256 "Library Manager"
        register_button_text_sha256 = Get-Utf8Sha256 "このフォルダーのスキルを登録"
        gui_visible = $true
        gui_title = $gui.Current.Name
        project_binding_visible = $projectBound
        selection_choice_count = @($selectionContract.labels).Count
        selection_combo_exact_match_count = [int]$selectionContract.exact_match_count
        library_manager_button_count = if (
            [bool]$managerButton.viewable -and
            [string]$managerButton.text_sha256 -ceq (Get-Utf8Sha256 "Library Manager")
        ) { 1 } else { 0 }
        register_button_count = if (
            [bool]$registerButton.viewable -and
            [string]$registerButton.text_sha256 -ceq
                (Get-Utf8Sha256 "このフォルダーのスキルを登録")
        ) { 1 } else { 0 }
    }
    Assert-Field $projectBound `
        "Receipt-bound context UI target digest does not bind the Explorer-selected folder."
    Assert-Field $projectSemanticVisible `
        "Receipt-bound context UI does not display the expected selected/projectless state."
    Assert-Field ($observation.library_manager_button_count -eq 1) `
        "Receipt-bound context UI does not expose one Library Manager button."
    Assert-Field ($observation.register_button_count -eq 1) `
        "Receipt-bound context UI does not expose one registration button."
    if ($TranscriptSource) {
        Add-UiaTranscriptEvent "unified_gui_observed" $TranscriptSource ([ordered]@{
            element = Get-UiaElementSnapshot $gui
            project_sha256 = Get-Utf16Sha256 $ProjectPath
            gui_visible = $observation.gui_visible
            gui_title = $observation.gui_title
            project_binding_visible = $observation.project_binding_visible
            selection_choice_count = $observation.selection_choice_count
            selection_choice_values_sha256 = $observation.selection_choice_values_sha256
            selected_choice_value_sha256 = $observation.selected_choice_value_sha256
            selection_combo_exact_match_count = $observation.selection_combo_exact_match_count
            library_manager_button_count = $observation.library_manager_button_count
            library_manager_button_text_sha256 = `
                $observation.library_manager_button_text_sha256
            register_button_count = $observation.register_button_count
            register_button_text_sha256 = $observation.register_button_text_sha256
        })
    }
    $observation
}

function Close-UiaWindow($Element) {
    $window = Get-Pattern $Element ([System.Windows.Automation.WindowPattern]::Pattern)
    Assert-Field ($null -ne $window) "Visible Skill Magnet window has no WindowPattern."
    $window.Close()
    Start-Sleep -Milliseconds 300
}

function Get-VisibleWindowsByPrefix([string]$Prefix, [int]$ProcessId = 0) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window
    )
    $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children, $condition
    )
    @($windows | Where-Object {
        try {
            -not $_.Current.IsOffscreen -and
            ($ProcessId -le 0 -or [int]$_.Current.ProcessId -eq $ProcessId) -and
            $_.Current.Name.StartsWith($Prefix, [StringComparison]::Ordinal)
        }
        catch { $false }
    })
}

function Wait-VisibleWindowByPrefix(
    [string]$Prefix,
    [int]$ProcessId = 0,
    [int]$Seconds = 30
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $matches = @(Get-VisibleWindowsByPrefix $Prefix $ProcessId)
        if ($matches.Count -gt 0) { return $matches[0] }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "UI Automation window did not appear: $Prefix"
}

function Wait-VisibleWindowClosed([int]$ProcessId, [string]$Prefix, [int]$Seconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $matches = @(Get-VisibleWindowsByPrefix $Prefix | Where-Object {
            try { [int]$_.Current.ProcessId -eq $ProcessId } catch { $false }
        })
        if ($matches.Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Window remained visible after close: $Prefix"
}

function Wait-ProcessExited([int]$ProcessId, [int]$Seconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Explorer-launched process remained alive after its UI was closed: $ProcessId"
}

function Get-FieldProcessIdentity([int]$TargetProcessId) {
    $process = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        $path = [IO.Path]::GetFullPath([string]$process.Path)
        $started = $process.StartTime.ToUniversalTime().Ticks
    }
    catch {
        # A process which cannot be identified is never safe for the collector
        # to claim or terminate.
        return $null
    }
    [ordered]@{
        process_id = [int]$process.Id
        executable_path = $path
        start_time_utc_ticks = [long]$started
    }
}

function Test-FieldProcessIdentity([System.Collections.IDictionary]$Identity) {
    if ($null -eq $Identity) { return $false }
    $current = Get-FieldProcessIdentity ([int]$Identity.process_id)
    if ($null -eq $current) { return $false }
    (
        [string]$current.executable_path -ieq [string]$Identity.executable_path -and
        [long]$current.start_time_utc_ticks -eq [long]$Identity.start_time_utc_ticks
    )
}

function Read-FieldContextOwner() {
    if (-not $script:FieldContextOwnerPath -or -not (
        Test-Path -LiteralPath $script:FieldContextOwnerPath -PathType Leaf
    )) { return $null }
    try {
        Get-Content -LiteralPath $script:FieldContextOwnerPath -Raw |
            ConvertFrom-Json
    }
    catch { return $null }
}

function Read-ValidatedFieldContextOwner() {
    if (-not (Test-Path -LiteralPath $script:FieldContextOwnerPath -PathType Leaf)) {
        return $null
    }
    $item = Get-Item -LiteralPath $script:FieldContextOwnerPath -Force
    $parentItem = Get-Item -LiteralPath $item.Directory.FullName -Force
    Assert-Field (
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and
        ($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
    ) "Context UI owner receipt and its state directory must not be reparse points."
    $stream = [IO.FileStream]::new(
        $item.FullName,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        Assert-Field ($stream.Length -gt 0 -and $stream.Length -le 262144) `
            "Context UI owner receipt size is outside the accepted range."
        $buffer = [IO.MemoryStream]::new()
        try {
            $stream.CopyTo($buffer)
            $beforeBytes = $buffer.ToArray()
        }
        finally { $buffer.Dispose() }
        $pinnedItem = Get-Item -LiteralPath $script:FieldContextOwnerPath -Force
        Assert-Field (
            ($pinnedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and
            [long]$pinnedItem.Length -eq $stream.Length
        ) "Context UI owner receipt path changed while its pinned handle was open."
    }
    finally { $stream.Dispose() }
    $encoded = [Convert]::ToBase64String($beforeBytes)
    $validator = @'
import base64
import json
import sys

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result

payload = base64.b64decode("__FIELD_OWNER_BASE64__", validate=True)
value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
if not isinstance(value, dict):
    raise ValueError("owner receipt root must be an object")

OWNER_KEYS = {
    "schema_version", "owner_kind", "pid", "process_instance_id",
    "process_started_at_unix_ns", "target_sha256", "generation", "phase",
    "window_handle", "revision", "published_at_utc",
}
SURFACE_KEYS = {
    "schema_version", "generation", "pid", "phase", "window", "state",
    "widgets", "revision", "published_at_utc",
}
RECT_KEYS = {"x", "y", "width", "height"}
WIDGET_REQUIRED = {"id", "role", "state", "viewable", "hwnd", "client", "screen"}
WIDGET_OPTIONAL = {"text_sha256", "value_sha256", "values_sha256"}

def exact_object(item, keys, label):
    if not isinstance(item, dict) or set(item) != keys:
        raise ValueError(label + " keys do not match the exact receipt schema")
    return item

def exact_int(item, label, positive=False):
    if type(item) is not int or (positive and item <= 0):
        raise ValueError(label + " must be an exact integer")
    return item

def exact_bool(item, label):
    if type(item) is not bool:
        raise ValueError(label + " must be an exact boolean")

def text(item, label, pattern=None):
    import re
    if not isinstance(item, str) or (pattern and not re.fullmatch(pattern, item)):
        raise ValueError(label + " must be a valid string")
    return item

def rectangle(item, label):
    item = exact_object(item, RECT_KEYS, label)
    for key in RECT_KEYS:
        exact_int(item[key], label + "." + key)

owner_keys = OWNER_KEYS | ({"ui_surface"} if "ui_surface" in value else set())
owner = exact_object(value, owner_keys, "owner")
if exact_int(owner["schema_version"], "owner.schema_version") != 2:
    raise ValueError("owner.schema_version must be 2")
if text(owner["owner_kind"], "owner.owner_kind") != "context_launcher":
    raise ValueError("owner.owner_kind must be context_launcher")
exact_int(owner["pid"], "owner.pid", True)
text(owner["process_instance_id"], "owner.process_instance_id", r"[0-9a-f]{32}")
exact_int(owner["process_started_at_unix_ns"], "owner.process_started_at_unix_ns", True)
text(owner["target_sha256"], "owner.target_sha256", r"[0-9a-f]{64}")
text(owner["generation"], "owner.generation", r"[0-9a-f]{32}")
phase = text(owner["phase"], "owner.phase")
if phase not in {"context_starting", "context_selection", "library_manager"}:
    raise ValueError("owner.phase is unsupported")
exact_int(owner["window_handle"], "owner.window_handle")
exact_int(owner["revision"], "owner.revision", True)
text(owner["published_at_utc"], "owner.published_at_utc")
if "ui_surface" not in owner and phase != "context_starting":
    raise ValueError(phase + " owner must contain ui_surface")
if "ui_surface" in owner:
    if phase not in {"context_selection", "library_manager"}:
        raise ValueError("starting owner must not contain ui_surface")
    surface = exact_object(owner["ui_surface"], SURFACE_KEYS, "ui_surface")
    if exact_int(surface["schema_version"], "ui_surface.schema_version") != 1:
        raise ValueError("ui_surface.schema_version must be 1")
    if surface["generation"] != owner["generation"]:
        raise ValueError("ui_surface.generation mismatch")
    if type(surface["pid"]) is not int or surface["pid"] != owner["pid"]:
        raise ValueError("ui_surface.pid mismatch")
    if not isinstance(surface["phase"], str) or surface["phase"] != phase:
        raise ValueError("ui_surface.phase mismatch")
    if type(surface["revision"]) is not int or surface["revision"] != owner["revision"]:
        raise ValueError("ui_surface.revision mismatch")
    if (not isinstance(surface["published_at_utc"], str) or
            surface["published_at_utc"] != owner["published_at_utc"]):
        raise ValueError("ui_surface publication mismatch")
    window = exact_object(
        surface["window"], {"hwnd", "title_sha256", "client", "screen"}, "window"
    )
    if type(window["hwnd"]) is not int or window["hwnd"] != owner["window_handle"]:
        raise ValueError("window.hwnd mismatch")
    text(window["title_sha256"], "window.title_sha256", r"[0-9a-f]{64}")
    rectangle(window["client"], "window.client")
    rectangle(window["screen"], "window.screen")
    if phase == "context_selection":
        state = exact_object(
            surface["state"],
            {"language_sha256", "selection_mode_sha256", "processing", "details_visible"},
            "state",
        )
        text(state["language_sha256"], "state.language_sha256", r"[0-9a-f]{64}")
        text(state["selection_mode_sha256"], "state.selection_mode_sha256", r"[0-9a-f]{64}")
        exact_bool(state["processing"], "state.processing")
        exact_bool(state["details_visible"], "state.details_visible")
    else:
        state = surface["state"]
        if not isinstance(state, dict) or set(state) not in (
                {"processing", "register_selected"},
                {"processing", "register_selected", "stage_sha256"}):
            raise ValueError("state keys do not match library_manager schema")
        exact_bool(state["processing"], "state.processing")
        exact_bool(state["register_selected"], "state.register_selected")
        if "stage_sha256" in state:
            text(state["stage_sha256"], "state.stage_sha256", r"[0-9a-f]{64}")
    widgets = surface["widgets"]
    if not isinstance(widgets, list):
        raise ValueError("widgets must be an array")
    for index, widget in enumerate(widgets):
        label = "widgets[" + str(index) + "]"
        if not isinstance(widget, dict):
            raise ValueError(label + " must be an object")
        keys = set(widget)
        if not (WIDGET_REQUIRED <= keys <= WIDGET_REQUIRED | WIDGET_OPTIONAL):
            raise ValueError(label + " keys do not match exact schema")
        text(widget["id"], label + ".id")
        text(widget["role"], label + ".role")
        exact_bool(widget["viewable"], label + ".viewable")
        exact_int(widget["hwnd"], label + ".hwnd", True)
        rectangle(widget["client"], label + ".client")
        rectangle(widget["screen"], label + ".screen")
        widget_state = exact_object(
            widget["state"], {"configured", "enabled"}, label + ".state"
        )
        text(widget_state["configured"], label + ".state.configured")
        exact_bool(widget_state["enabled"], label + ".state.enabled")
        if widget["id"] == "request" and keys & WIDGET_OPTIONAL:
            raise ValueError("request widget must not contain content digests")
        for key in keys & WIDGET_OPTIONAL:
            text(widget[key], label + "." + key, r"[0-9a-f]{64}")
print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
'@
    $validatorProgram = $validator.Replace("__FIELD_OWNER_BASE64__", $encoded)
    $validatedJson = $validatorProgram |
        & $script:FieldExpectedExecutablePath -I - | Out-String
    Assert-Field ($LASTEXITCODE -eq 0) `
        "Context UI owner receipt is not strict duplicate-free UTF-8 JSON."
    [ordered]@{
        owner = $validatedJson | ConvertFrom-Json
        sha256 = Get-BytesSha256 $beforeBytes
    }
}

function Get-FieldUiSurfaceWidget($Surface, [string]$Id, [string]$Role = "") {
    $matches = @($Surface.widgets | Where-Object { [string]$_.id -ceq $Id })
    Assert-Field ($matches.Count -eq 1) `
        "UI receipt must expose exactly one '$Id' widget; observed $($matches.Count)."
    $widget = $matches[0]
    if ($Role) {
        Assert-Field ([string]$widget.role -ceq $Role) `
            "UI receipt widget '$Id' role mismatch: $($widget.role)"
    }
    $widget
}

function Test-FieldScreenRectangle($Actual, $Expected, [int]$Tolerance = 2) {
    if ($null -eq $Actual -or $null -eq $Expected) { return $false }
    foreach ($key in @("x", "y", "width", "height")) {
        $actualValue = 0
        $expectedValue = 0
        if (-not [int]::TryParse([string]$Actual.$key, [ref]$actualValue) -or
            -not [int]::TryParse([string]$Expected.$key, [ref]$expectedValue) -or
            [Math]::Abs($actualValue - $expectedValue) -gt $Tolerance) {
            return $false
        }
    }
    $true
}

function Test-FieldRectangleWithin($Inner, $Outer) {
    if ($null -eq $Inner -or $null -eq $Outer) { return $false }
    $innerRight = [int]$Inner.x + [int]$Inner.width
    $innerBottom = [int]$Inner.y + [int]$Inner.height
    $outerRight = [int]$Outer.x + [int]$Outer.width
    $outerBottom = [int]$Outer.y + [int]$Outer.height
    (
        [int]$Inner.width -gt 0 -and [int]$Inner.height -gt 0 -and
        [int]$Inner.x -ge [int]$Outer.x -and [int]$Inner.y -ge [int]$Outer.y -and
        $innerRight -le $outerRight -and $innerBottom -le $outerBottom
    )
}

function Get-UiaScreenRectangle($Element) {
    $rectangle = $Element.Current.BoundingRectangle
    [ordered]@{
        x = [int]$rectangle.Left
        y = [int]$rectangle.Top
        width = [int]$rectangle.Width
        height = [int]$rectangle.Height
    }
}

function Wait-FieldUiSurface(
    [int]$ExpectedProcessId,
    [string]$ExpectedPhase,
    $TopLevelWindow,
    [int]$Seconds = 12
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $validatedOwner = Read-ValidatedFieldContextOwner
        if ($null -eq $validatedOwner) {
            Start-Sleep -Milliseconds 100
            continue
        }
        $owner = $validatedOwner.owner
        Assert-NoRawReceiptDisplayValues $owner
        if ([int]$owner.pid -ne $ExpectedProcessId) {
            throw (
                "Context UI receipt belongs to unexpected process: " +
                "expected=$ExpectedProcessId; observed=$($owner.pid); " +
                "generation=$($owner.generation)"
            )
        }
        $generation = [string]$owner.generation
        Assert-Field ($generation -match '^[0-9a-f]{32}$') `
            "Context UI receipt generation is invalid: $generation"
        Assert-Field ($generation -ne $script:FieldPreExistingOwnerGeneration) `
            "Context UI receipt reused the pre-field owner generation."
        $script:FieldOwnedOwnerTokens[$generation] = [ordered]@{
            process_id = $ExpectedProcessId
            invocation_id = [string](
                $script:FieldOwnedProcessIdentities[[string]$ExpectedProcessId].invocation_id
            )
        }
        if ([string]$owner.phase -ne $ExpectedPhase -or $null -eq $owner.ui_surface) {
            Start-Sleep -Milliseconds 100
            continue
        }
        $surface = $owner.ui_surface
        Assert-Field (
            [int]$owner.schema_version -eq 2 -and
            [string]$owner.owner_kind -ceq "context_launcher" -and
            [string]$owner.process_instance_id -match '^[0-9a-f]{32}$' -and
            [int64]$owner.process_started_at_unix_ns -gt 0 -and
            [string]$owner.target_sha256 -match '^[0-9a-f]{64}$'
        ) "Context UI owner identity schema is incomplete or invalid."
        $receiptProcessIdentity = $script:FieldOwnedProcessIdentities[
            [string]$ExpectedProcessId
        ]
        Assert-Field (
            $null -ne $receiptProcessIdentity -and
            (Test-FieldProcessIdentity $receiptProcessIdentity)
        ) "Context UI receipt is not bound to the field-owned executable/start identity."
        $unixEpochTicks = [DateTime]::new(
            1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc
        ).Ticks
        $observedProcessStartNs = [int64](
            ([int64]$receiptProcessIdentity.start_time_utc_ticks - $unixEpochTicks) * 100
        )
        $ownerProcessStartNs = [int64]$owner.process_started_at_unix_ns
        Assert-Field (
            $ownerProcessStartNs -ge ($observedProcessStartNs - [int64]5000000000) -and
            $ownerProcessStartNs -le ($observedProcessStartNs + [int64]120000000000)
        ) "Context UI receipt process-start marker does not match the live process epoch."
        Assert-Field ([int]$surface.schema_version -eq 1) `
            "Context UI receipt schema version is not 1."
        Assert-Field ([int64]$surface.revision -gt 0) `
            "Context UI receipt revision is not positive."
        Assert-Field (
            [int64]$owner.revision -eq [int64]$surface.revision -and
            [string]$owner.published_at_utc -ceq [string]$surface.published_at_utc
        ) "Context UI owner and nested surface revisions do not match."
        $publishedText = [string]$surface.published_at_utc
        Assert-Field (
            $publishedText -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$'
        ) "Context UI receipt publication time is not canonical UTC Z format."
        $publishedAt = [DateTime]::MinValue
        Assert-Field (
            [DateTime]::TryParse(
                [string]$surface.published_at_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                ([Globalization.DateTimeStyles]::AdjustToUniversal -bor
                 [Globalization.DateTimeStyles]::AssumeUniversal),
                [ref]$publishedAt
            ) -and
            $publishedAt.Kind -eq [DateTimeKind]::Utc -and
            $publishedAt -le [DateTime]::UtcNow.AddSeconds(2) -and
            $publishedAt -ge [DateTime]::UtcNow.AddMinutes(-2)
        ) "Context UI receipt publication time is stale or invalid."
        Assert-Field (
            [string]$surface.generation -ceq $generation -and
            [int]$surface.pid -eq $ExpectedProcessId -and
            [string]$surface.phase -ceq $ExpectedPhase
        ) "Context UI receipt identity does not match its lease owner."
        $ownerHandle = [int64]$owner.window_handle
        $surfaceHandle = [int64]$surface.window.hwnd
        $uiaSnapshot = Get-UiaElementSnapshot $TopLevelWindow
        Assert-Field (
            $ownerHandle -gt 0 -and $surfaceHandle -eq $ownerHandle -and
            [int64]$uiaSnapshot.native_window_handle -eq $ownerHandle -and
            [int]$uiaSnapshot.process_id -eq $ExpectedProcessId
        ) "Context UI receipt HWND/PID does not match the observed top-level window."
        $nativePid = [uint32]0
        $nativeHandle = [IntPtr]$ownerHandle
        Assert-Field ([SkillMagnetFieldInput]::IsWindow($nativeHandle)) `
            "Context UI receipt top-level HWND is not a live window."
        $null = [SkillMagnetFieldInput]::GetWindowThreadProcessId(
            $nativeHandle, [ref]$nativePid
        )
        Assert-Field ([int]$nativePid -eq $ExpectedProcessId) `
            "Context UI receipt top-level HWND belongs to another process."
        Assert-Field (
            [SkillMagnetFieldInput]::GetAncestor($nativeHandle, 2) -eq $nativeHandle
        ) "Context UI receipt HWND is not a top-level root window."
        $nativeTitle = [SkillMagnetFieldInput]::WindowText($nativeHandle)
        $expectedTitle = switch ($ExpectedPhase) {
            "context_selection" { "Skill Magnet — 実行確認" }
            "library_manager" { "Library Manager" }
            default { throw "Unsupported UI receipt phase: $ExpectedPhase" }
        }
        Assert-Field (
            $nativeTitle -ceq $expectedTitle -and
            [string]$surface.window.title_sha256 -ceq (Get-Utf8Sha256 $nativeTitle) -and
            [string]$uiaSnapshot.name -ceq $nativeTitle
        ) "Context UI receipt title does not match the phase-authorized live root window."
        $nativeRectangle = [SkillMagnetFieldInput+RECT]::new()
        Assert-Field ([SkillMagnetFieldInput]::GetWindowRect(
            $nativeHandle, [ref]$nativeRectangle
        )) "Could not read the live root-window rectangle."
        $nativeScreen = [ordered]@{
            x = $nativeRectangle.Left
            y = $nativeRectangle.Top
            width = $nativeRectangle.Right - $nativeRectangle.Left
            height = $nativeRectangle.Bottom - $nativeRectangle.Top
        }
        Assert-Field (Test-FieldScreenRectangle $nativeScreen $surface.window.screen 0) `
            "Context UI receipt root rectangle differs from the live Win32 window."
        $virtualScreen = [ordered]@{
            x = [SkillMagnetFieldInput]::GetSystemMetrics(76)
            y = [SkillMagnetFieldInput]::GetSystemMetrics(77)
            width = [SkillMagnetFieldInput]::GetSystemMetrics(78)
            height = [SkillMagnetFieldInput]::GetSystemMetrics(79)
        }
        Assert-Field (Test-FieldRectangleWithin $nativeScreen $virtualScreen) `
            "Context UI receipt root window is outside the virtual screen."
        $widgetIds = @($surface.widgets | ForEach-Object { [string]$_.id })
        Assert-Field (
            $widgetIds.Count -gt 0 -and
            @($widgetIds | Sort-Object -Unique).Count -eq $widgetIds.Count
        ) "Context UI receipt widget ids are missing or duplicated."
        $requestWidgets = @($surface.widgets | Where-Object { [string]$_.id -ceq "request" })
        if ($ExpectedPhase -ceq "context_selection") {
            Assert-Field ($requestWidgets.Count -eq 1) `
                "Context UI receipt must describe exactly one request widget."
            $requestFields = @($requestWidgets[0].PSObject.Properties.Name)
            foreach ($forbiddenRequestField in @(
                "text", "value", "values", "text_sha256", "value_sha256", "values_sha256"
            )) {
                Assert-Field ($requestFields -notcontains $forbiddenRequestField) `
                    "Context UI receipt must not persist request content or its digest."
            }
        }
        else {
            Assert-Field ($requestWidgets.Count -eq 0) `
                "Non-context UI receipt unexpectedly contains a request widget."
        }
        $uiaChildren = @($TopLevelWindow.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        ))
        foreach ($widget in @($surface.widgets | Where-Object { [bool]$_.viewable })) {
            $widgetHandle = [int64]$widget.hwnd
            Assert-Field (
                [string]$widget.id -and [string]$widget.role -and
                $widgetHandle -gt 0 -and [int]$widget.screen.width -gt 0 -and
                [int]$widget.screen.height -gt 0
            ) "A visible UI receipt widget has incomplete identity or geometry."
            Assert-Field (
                (Test-FieldRectangleWithin $widget.screen $nativeScreen) -and
                (Test-FieldRectangleWithin $widget.screen $virtualScreen)
            ) "UI receipt widget '$($widget.id)' lies outside its root or virtual screen."
            $widgetNativePid = [uint32]0
            $null = [SkillMagnetFieldInput]::GetWindowThreadProcessId(
                [IntPtr]$widgetHandle, [ref]$widgetNativePid
            )
            Assert-Field ([int]$widgetNativePid -eq $ExpectedProcessId) `
                "UI receipt widget '$($widget.id)' HWND belongs to another process."
            Assert-Field (
                [SkillMagnetFieldInput]::GetAncestor([IntPtr]$widgetHandle, 2) -eq
                $nativeHandle
            ) "UI receipt widget '$($widget.id)' does not belong to the receipt root HWND."
            $uiaMatches = @($uiaChildren | Where-Object {
                try {
                    [int64]$_.Current.NativeWindowHandle -eq $widgetHandle -and
                    [string]$_.Current.ClassName -ceq "TkChild" -and
                    -not [bool]$_.Current.IsOffscreen -and
                    (Test-FieldScreenRectangle `
                        (Get-UiaScreenRectangle $_) $widget.screen 0)
                } catch { $false }
            })
            Assert-Field ($uiaMatches.Count -eq 1) `
                ("UI receipt widget '$($widget.id)' is not bound to exactly one " +
                 "live UIAutomation child; observed $($uiaMatches.Count).")
        }
        return [ordered]@{
            owner = $owner
            surface = $surface
            element = $TopLevelWindow
            element_snapshot = $uiaSnapshot
            receipt_sha256 = Get-BytesSha256 (
                [Text.UTF8Encoding]::new($false).GetBytes(
                    (ConvertTo-Json $surface -Depth 12 -Compress)
                )
            )
            owner_sha256 = [string]$validatedOwner.sha256
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Context UI receipt did not publish phase '$ExpectedPhase' for PID $ExpectedProcessId."
}

function Invoke-FieldUiSurfaceWidget(
    [int]$ExpectedProcessId,
    [string]$ExpectedPhase,
    $TopLevelWindow,
    [string]$ExpectedGeneration,
    [string]$Id,
    [string]$ExpectedTargetSha256,
    [string]$ExpectedNextPhase = "",
    [string]$ExpectedNextTitlePrefix = ""
) {
    # The field workflow is observational.  Only its two explicit navigation
    # actions are eligible for physical mouse input; CRUD controls are inspected
    # but never clicked or admitted through this boundary.
    $expectedTextById = @{
        library_manager = "Library Manager"
        register_selected = "このフォルダーのスキルを登録"
    }
    $allowedIds = @($expectedTextById.Keys)
    Assert-Field ($allowedIds -contains $Id) `
        "UI receipt click id is not allowlisted: $Id"
    $expectedWidgetText = [string]$expectedTextById[$Id]
    $expectedWidgetTextSha256 = Get-Utf8Sha256 $expectedWidgetText
    for ($attempt = 0; $attempt -lt 3; $attempt += 1) {
        $receipt = Wait-FieldUiSurface $ExpectedProcessId $ExpectedPhase $TopLevelWindow
        Assert-Field ([string]$receipt.owner.generation -ceq $ExpectedGeneration) `
            "UI receipt generation changed before '$Id' was invoked."
        Assert-Field (
            [string]$receipt.owner.target_sha256 -ceq $ExpectedTargetSha256
        ) "UI receipt target changed before '$Id' was invoked."
        $widget = Get-FieldUiSurfaceWidget $receipt.surface $Id "button"
        Assert-Field (
            [bool]$widget.viewable -and [bool]$widget.state.enabled -and
            [string]$widget.text_sha256 -ceq $expectedWidgetTextSha256
        ) "UI receipt widget '$Id' is not the expected visible/enabled action."
        $x = [int]$widget.screen.x + [int]([int]$widget.screen.width / 2)
        $y = [int]$widget.screen.y + [int]([int]$widget.screen.height / 2)
        $windowHandle = [IntPtr]([int64]$receipt.owner.window_handle)
        $widgetHandle = [IntPtr]([int64]$widget.hwnd)
        $identity = $script:FieldOwnedProcessIdentities[[string]$ExpectedProcessId]
        Assert-Field ($null -ne $identity -and (Test-FieldProcessIdentity $identity)) `
            "Receipt-bound process identity changed before '$Id'."
        Assert-Field ([SkillMagnetFieldInput]::FocusWindow($windowHandle)) `
            "Could not foreground the receipt-bound window before '$Id'."
        Start-Sleep -Milliseconds 100
        Assert-Field ([SkillMagnetFieldInput]::GetForegroundWindow() -eq $windowHandle) `
            "Receipt-bound window did not remain foreground before '$Id'."
        $point = [SkillMagnetFieldInput+POINT]::new()
        $point.X = $x
        $point.Y = $y
        $firstHit = [SkillMagnetFieldInput]::WindowFromPoint($point)
        Assert-Field ($firstHit -eq $widgetHandle) `
            "Receipt-bound '$Id' center is covered or does not hit its exact widget HWND."
        Assert-Field ([SkillMagnetFieldInput]::SetCursorPos($x, $y)) `
            "Could not move the cursor to receipt-bound '$Id'."
        $fresh = Wait-FieldUiSurface $ExpectedProcessId $ExpectedPhase $TopLevelWindow
        $freshWidget = Get-FieldUiSurfaceWidget $fresh.surface $Id "button"
        $unchanged = (
            [string]$fresh.owner.generation -ceq $ExpectedGeneration -and
            [string]$fresh.owner.target_sha256 -ceq $ExpectedTargetSha256 -and
            [int64]$fresh.surface.revision -eq [int64]$receipt.surface.revision -and
            [string]$fresh.owner_sha256 -ceq [string]$receipt.owner_sha256 -and
            [int64]$freshWidget.hwnd -eq [int64]$widget.hwnd -and
            [string]$freshWidget.text_sha256 -ceq $expectedWidgetTextSha256 -and
            [bool]$freshWidget.viewable -and [bool]$freshWidget.state.enabled -and
            (Test-FieldScreenRectangle $freshWidget.screen $widget.screen 0)
        )
        if (-not $unchanged) { continue }
        Assert-Field (Test-FieldProcessIdentity $identity) `
            "Receipt-bound process identity changed after '$Id' revalidation."
        Assert-Field ([SkillMagnetFieldInput]::GetForegroundWindow() -eq $windowHandle) `
            "Receipt-bound window lost foreground before '$Id'."
        $secondHit = [SkillMagnetFieldInput]::WindowFromPoint($point)
        Assert-Field ($secondHit -eq $widgetHandle) `
            "Receipt-bound '$Id' hit-test changed before click."
        $uiaPoint = [System.Windows.Point]::new([double]$x, [double]$y)
        $uiaHit = [System.Windows.Automation.AutomationElement]::FromPoint($uiaPoint)
        Assert-Field (
            $null -ne $uiaHit -and
            [int64]$uiaHit.Current.NativeWindowHandle -eq [int64]$widget.hwnd -and
            [int]$uiaHit.Current.ProcessId -eq $ExpectedProcessId -and
            (Get-Utf8Sha256 ([string]$uiaHit.Current.Name)) -ceq $expectedWidgetTextSha256 -and
            [string]$uiaHit.Current.ClassName -ceq "TkChild" -and
            [bool]$uiaHit.Current.IsEnabled -and -not [bool]$uiaHit.Current.IsOffscreen -and
            (Test-FieldScreenRectangle (Get-UiaScreenRectangle $uiaHit) $widget.screen 0)
        ) "Receipt-bound '$Id' UIAutomation hit-test does not match its live Tk child."
        $pointPid = [uint32]0
        $null = [SkillMagnetFieldInput]::GetWindowThreadProcessId($secondHit, [ref]$pointPid)
        Assert-Field (
            [int]$pointPid -eq $ExpectedProcessId -and
            [SkillMagnetFieldInput]::GetAncestor($secondHit, 2) -eq $windowHandle
        ) "Receipt-bound '$Id' point belongs to another process or root window."
        $finalReceipt = Wait-FieldUiSurface `
            $ExpectedProcessId $ExpectedPhase $TopLevelWindow
        $finalWidget = Get-FieldUiSurfaceWidget $finalReceipt.surface $Id "button"
        if (-not (
            [string]$finalReceipt.owner.generation -ceq $ExpectedGeneration -and
            [string]$finalReceipt.owner.target_sha256 -ceq $ExpectedTargetSha256 -and
            [string]$finalReceipt.owner_sha256 -ceq [string]$fresh.owner_sha256 -and
            [int64]$finalReceipt.surface.revision -eq [int64]$fresh.surface.revision -and
            [int64]$finalWidget.hwnd -eq [int64]$widget.hwnd -and
            [string]$finalWidget.text_sha256 -ceq $expectedWidgetTextSha256 -and
            [bool]$finalWidget.viewable -and [bool]$finalWidget.state.enabled
        )) { continue }
        $clickHit = [SkillMagnetFieldInput]::WindowFromPoint($point)
        $clickUia = [System.Windows.Automation.AutomationElement]::FromPoint($uiaPoint)
        Assert-Field (
            [SkillMagnetFieldInput]::GetForegroundWindow() -eq $windowHandle -and
            $clickHit -eq $widgetHandle -and
            [SkillMagnetFieldInput]::GetAncestor($clickHit, 2) -eq $windowHandle -and
            $null -ne $clickUia -and
            [int64]$clickUia.Current.NativeWindowHandle -eq [int64]$widget.hwnd -and
            [int]$clickUia.Current.ProcessId -eq $ExpectedProcessId -and
            (Get-Utf8Sha256 ([string]$clickUia.Current.Name)) -ceq
                $expectedWidgetTextSha256 -and
            [string]$clickUia.Current.ClassName -ceq "TkChild" -and
            [bool]$clickUia.Current.IsEnabled -and -not [bool]$clickUia.Current.IsOffscreen
        ) "Receipt-bound '$Id' changed after final receipt validation; no mouse input was sent."
        Assert-Field (Test-FieldProcessIdentity $identity) `
            "Receipt-bound process identity changed immediately before '$Id'."
        Assert-Field ([SkillMagnetFieldInput]::CheckedClickCurrent(
            $x, $y, $widgetHandle, $windowHandle, [uint32]$ExpectedProcessId,
            [string]$identity.executable_path, [long]$identity.start_time_utc_ticks,
            $true, $expectedWidgetTextSha256,
            [string]$script:FieldContextOwnerPath,
            [string]$finalReceipt.owner_sha256,
            [string]$finalReceipt.owner.process_instance_id,
            [string]$ExpectedGeneration,
            [long]$finalReceipt.surface.revision,
            [string]$Id, $false
        )) "Receipt-bound '$Id' cursor/hit identity changed; no mouse input was sent."
        if ($ExpectedNextPhase -and $ExpectedNextTitlePrefix) {
            $nextWindow = Wait-VisibleWindowByPrefix `
                $ExpectedNextTitlePrefix $ExpectedProcessId 30
            $nextReceipt = Wait-FieldUiSurface `
                $ExpectedProcessId $ExpectedNextPhase $nextWindow 30
            Assert-Field (
                [string]$nextReceipt.owner.generation -ceq $ExpectedGeneration -and
                [string]$nextReceipt.owner.target_sha256 -ceq $ExpectedTargetSha256 -and
                [int64]$nextReceipt.surface.revision -gt [int64]$fresh.surface.revision
            ) "Receipt-bound '$Id' did not transition to a new '$ExpectedNextPhase' window."
            return [ordered]@{
                widget = $widget
                click_owner_receipt = $finalReceipt.owner
                next_window = $nextWindow
                next_surface = $nextReceipt.surface
            }
        }
        return [ordered]@{
            widget = $widget
            click_owner_receipt = $finalReceipt.owner
        }
    }
    throw "UI receipt changed repeatedly before '$Id'; no mouse input was sent."
}

function Register-FieldOwnedProcess([int]$TargetProcessId, [string]$InvocationId) {
    $identity = Get-FieldProcessIdentity $TargetProcessId
    if ($null -eq $identity) {
        # Fast duplicate launchers can exit before registration.  There is
        # nothing left for cleanup and no ownership is inferred from a PID.
        return
    }
    if ([string]$identity.executable_path -ine $script:FieldExpectedExecutablePath) {
        return
    }
    $key = [string]$TargetProcessId
    $preExisting = $script:FieldPreExistingProcessIdentities[$key]
    if ($null -ne $preExisting -and
        [long]$preExisting.start_time_utc_ticks -eq [long]$identity.start_time_utc_ticks -and
        [string]$preExisting.executable_path -ieq [string]$identity.executable_path) {
        # Never claim a process which existed before this field session.
        return
    }
    $identity.invocation_id = $InvocationId
    $script:FieldOwnedProcessIdentities[$key] = $identity

    $owner = Read-FieldContextOwner
    if ($null -ne $owner -and [int]$owner.pid -eq $TargetProcessId -and
        [string]$owner.generation -match '^[0-9a-f]{32}$' -and
        [string]$owner.generation -ne $script:FieldPreExistingOwnerGeneration) {
        $script:FieldOwnedOwnerTokens[[string]$owner.generation] = [ordered]@{
            process_id = $TargetProcessId
            invocation_id = $InvocationId
        }
    }
}

function Close-FieldOwnedUiAndReleaseLease() {
    # Close only windows whose process identity was observed in this field
    # session.  A title match alone must never close a user's pre-existing UI.
    $publishedOwner = Read-FieldContextOwner
    if ($null -ne $publishedOwner -and
        [string]$publishedOwner.generation -match '^[0-9a-f]{32}$' -and
        [string]$publishedOwner.generation -ne $script:FieldPreExistingOwnerGeneration) {
        $publishedIdentity = $script:FieldOwnedProcessIdentities[
            [string][int]$publishedOwner.pid
        ]
        if ($null -ne $publishedIdentity -and
            (Test-FieldProcessIdentity $publishedIdentity)) {
            # Record the generation while its exact PID/start-time/executable
            # identity is still alive.  Never infer ownership after exit.
            $script:FieldOwnedOwnerTokens[[string]$publishedOwner.generation] = [ordered]@{
                process_id = [int]$publishedOwner.pid
                invocation_id = [string]$publishedIdentity.invocation_id
            }
        }
    }
    for ($pass = 0; $pass -lt 3; $pass += 1) {
        $windowCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Window
        )
        $topLevel = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children, $windowCondition
        )
        foreach ($window in @($topLevel)) {
            try {
                $key = [string][int]$window.Current.ProcessId
                $identity = $script:FieldOwnedProcessIdentities[$key]
                if ($null -eq $identity -or -not (Test-FieldProcessIdentity $identity)) {
                    continue
                }
                $pattern = Get-Pattern $window ([System.Windows.Automation.WindowPattern]::Pattern)
                if ($null -ne $pattern) { $pattern.Close() }
            }
            catch {
                # Continue through every owned window; the identity-checked
                # process fallback below releases a lease after a broken UI.
            }
        }
        Start-Sleep -Milliseconds 300
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(3)
    do {
        $live = @($script:FieldOwnedProcessIdentities.Values | Where-Object {
            Test-FieldProcessIdentity $_
        })
        if ($live.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)

    foreach ($identity in @($script:FieldOwnedProcessIdentities.Values)) {
        if (-not (Test-FieldProcessIdentity $identity)) { continue }
        # This is not a broad Python kill: executable path, PID and immutable
        # process start time must all still match the field-owned child.
        Stop-Process -Id ([int]$identity.process_id) -Force -ErrorAction SilentlyContinue
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(3)
    do {
        $live = @($script:FieldOwnedProcessIdentities.Values | Where-Object {
            Test-FieldProcessIdentity $_
        })
        if ($live.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)

    $owner = Read-FieldContextOwner
    if ($null -ne $owner -and
        $script:FieldOwnedOwnerTokens.ContainsKey([string]$owner.generation)) {
        $token = $script:FieldOwnedOwnerTokens[[string]$owner.generation]
        $identity = $script:FieldOwnedProcessIdentities[[string][int]$token.process_id]
        if ($null -ne $identity -and -not (Test-FieldProcessIdentity $identity)) {
            # The OS byte lock has been released by process exit.  Remove only
            # the exact owner generation observed while that identity was live.
            Remove-Item -LiteralPath $script:FieldContextOwnerPath -Force -ErrorAction SilentlyContinue
        }
    }
    $remaining = @($script:FieldOwnedProcessIdentities.Values | Where-Object {
        Test-FieldProcessIdentity $_
    })
    if ($remaining.Count -gt 0) {
        $remainingIds = @($remaining | ForEach-Object {
            [string]$_.process_id
        }) -join ", "
        throw (
            "Field cleanup could not stop its owned process(es): $remainingIds. " +
            "Close only these PIDs in Task Manager, then rerun the field test."
        )
    }
}

function Get-VisibleDescendantText($Window) {
    $values = [Collections.Generic.List[string]]::new()
    try { $null = $values.Add([string]$Window.Current.Name) } catch { }
    foreach ($element in @($Window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    ))) {
        try {
            if (-not $element.Current.IsOffscreen -and $element.Current.Name) {
                $null = $values.Add([string]$element.Current.Name)
            }
        } catch { }
    }
    $values -join "`n"
}

function Get-TreeContentSha256([string]$Path) {
    if (-not [IO.Directory]::Exists($Path)) {
        return Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes("MISSING`n"))
    }
    $root = [IO.Path]::GetFullPath($Path)
    $entries = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $pending.Push([IO.DirectoryInfo]::new($root))
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($entry in @($directory.GetFileSystemInfos() | Sort-Object Name)) {
            $relative = [IO.Path]::GetRelativePath($root, $entry.FullName).Replace('\', '/')
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                $null = $entries.Add("L`t$relative")
                continue
            }
            if ($entry -is [IO.DirectoryInfo]) {
                $null = $entries.Add("D`t$relative")
                $pending.Push($entry)
                continue
            }
            $bytes = [IO.File]::ReadAllBytes($entry.FullName)
            $null = $entries.Add(
                "F`t$relative`t$($bytes.Length)`t$(Get-BytesSha256 $bytes)"
            )
        }
    }
    $payload = ((@($entries | Sort-Object) -join "`n") + "`n")
    Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($payload))
}

function Get-PersistentMutationSnapshot([string]$ConfigPath, [string]$StateRoot) {
    [ordered]@{
        config_sha256 = Get-BytesSha256 ([IO.File]::ReadAllBytes($ConfigPath))
        library_sha256 = Get-TreeContentSha256 (Join-Path $StateRoot "library")
        transactions_sha256 = Get-TreeContentSha256 (Join-Path $StateRoot "library-transactions")
    }
}

function Test-SnapshotEqual(
    [System.Collections.IDictionary]$Before,
    [System.Collections.IDictionary]$After
) {
    $Before.config_sha256 -eq $After.config_sha256 -and
    $Before.library_sha256 -eq $After.library_sha256 -and
    $Before.transactions_sha256 -eq $After.transactions_sha256
}

function Inspect-LibraryManager(
    [string]$ExpectedRemote,
    [int]$ExpectedProcessId
) {
    $manager = Wait-VisibleWindowByPrefix "Library Manager" $ExpectedProcessId
    $receipt = Wait-FieldUiSurface $ExpectedProcessId "library_manager" $manager
    $surface = $receipt.surface
    $remoteWidget = Get-FieldUiSurfaceWidget $surface "configured_remote" "entry"
    $expectedRemoteSha256 = Get-Utf8Sha256 $ExpectedRemote
    $remoteVisible = (
        [bool]$remoteWidget.viewable -and
        [string]$remoteWidget.value_sha256 -ceq $expectedRemoteSha256
    )
    Assert-Field $remoteVisible `
        "Library Manager configured-remote digest does not match release configuration."
    $sourceWidget = Get-FieldUiSurfaceWidget $surface "registration_source" "entry"
    Assert-Field ([string]$sourceWidget.value_sha256 -match '^[0-9a-f]{64}$') `
        "Library Manager registration-source digest is unavailable."
    $buttonContracts = [ordered]@{
        new_registration = "新規登録"
        update = "選択項目を更新"
        delete = "選択項目を削除"
        reload = "再読込"
    }
    $crud = [ordered]@{
        create_button_count = 0
        update_button_count = 0
        delete_button_count = 0
        reload_button_count = 0
    }
    $buttonTextHashes = [ordered]@{}
    foreach ($identifier in @($buttonContracts.Keys)) {
        $button = Get-FieldUiSurfaceWidget $surface $identifier "button"
        $expectedButtonText = [string]$buttonContracts[$identifier]
        $expectedButtonSha256 = Get-Utf8Sha256 $expectedButtonText
        Assert-Field (
            [bool]$button.viewable -and
            [string]$button.text_sha256 -ceq $expectedButtonSha256
        ) "Library Manager CRUD control '$identifier' is not uniquely visible."
        $crudKey = if ($identifier -ceq "new_registration") {
            "create_button_count"
        } else { "${identifier}_button_count" }
        $crud[$crudKey] = 1
        $buttonTextHashes[$identifier] = $expectedButtonSha256
    }
    [ordered]@{
        element = $manager
        element_snapshot = Get-UiaElementSnapshot $manager
        ui_surface = $surface
        ui_owner_receipt = $receipt.owner
        ui_surface_generation = [string]$receipt.owner.generation
        configured_remote_sha256 = $expectedRemoteSha256
        configured_remote_visible = $remoteVisible
        create_button_count = $crud.create_button_count
        update_button_count = $crud.update_button_count
        delete_button_count = $crud.delete_button_count
        reload_button_count = $crud.reload_button_count
        create_button_text_sha256 = [string]$buttonTextHashes.new_registration
        update_button_text_sha256 = [string]$buttonTextHashes.update
        delete_button_text_sha256 = [string]$buttonTextHashes.delete
        reload_button_text_sha256 = [string]$buttonTextHashes.reload
        registration_source_sha256 = [string]$sourceWidget.value_sha256
    }
}

function New-UiReceiptEvidence(
    [string]$Role,
    $Observation,
    [string]$ClaimWidgetId,
    [string]$ClaimField
) {
    Assert-Field ($ClaimField -in @("text_sha256", "value_sha256", "values_sha256")) `
        "Receipt claim field is not an approved digest field."
    $receipt = if ($Observation.PSObject.Properties.Name -contains "click_owner_receipt") {
        $Observation.click_owner_receipt
    } else {
        $Observation.ui_owner_receipt
    }
    Assert-NoRawReceiptDisplayValues $receipt
    $widget = Get-FieldUiSurfaceWidget $receipt.ui_surface $ClaimWidgetId
    $claim = [string]$widget.$ClaimField
    Assert-Field ($claim -match '^[0-9a-f]{64}$') `
        "Receipt claim digest is missing for role '$Role'."
    [ordered]@{
        role = $Role
        phase = [string]$receipt.phase
        process_instance_id = [string]$receipt.process_instance_id
        generation = [string]$receipt.generation
        revision = [int64]$receipt.revision
        claim_widget_id = $ClaimWidgetId
        claim_field = $ClaimField
        claim_sha256 = $claim
        receipt_sha256 = Get-CanonicalJsonSha256 $receipt
        surface_sha256 = Get-CanonicalJsonSha256 $receipt.ui_surface
        receipt = $receipt
    }
}

function Wait-MissingSkillRecoveryDialog([int]$ExpectedProcessId, [int]$Seconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $windowCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Window
        )
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children, $windowCondition
        )
        foreach ($window in @($windows)) {
            try {
                if ($window.Current.IsOffscreen -or [int]$window.Current.ProcessId -ne $ExpectedProcessId) {
                    continue
                }
                $text = Get-VisibleDescendantText $window
                if ($text -notlike "*SKILL.md*") { continue }
                $specific = $text -like "*選択したフォルダー内*"
                $actionable = $text -like "*次の操作*" -and (
                    $text -like "*再実行*" -or
                    $text -like "*実行してください*" -or
                    $text -like "*選び直*"
                )
                Assert-Field $specific "Missing-SKILL.md dialog does not state the selected-folder cause."
                Assert-Field $actionable "Missing-SKILL.md dialog has no concrete recovery action."
                Assert-Field ((Get-ButtonCount $window "OK") -eq 1) `
                    "Missing-SKILL.md dialog must expose one OK button."
                return [ordered]@{
                    element = $window
                    element_snapshot = Get-UiaElementSnapshot $window
                    missing_skill_cause_visible = $specific
                    actionable_recovery_visible = $actionable
                    ok_button_count = 1
                }
            } catch { throw }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Specific missing-SKILL.md recovery dialog did not appear."
}

function Read-InvokeLines([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    @([IO.File]::ReadAllText($Path, [Text.Encoding]::Unicode) -split "`r?`n" |
        Where-Object { $_ })
}

function Parse-InvokeLine([string]$Line) {
    $values = @{}
    $parts = $Line -split "`t"
    $values.timestamp = $parts[0]
    foreach ($part in $parts[1..($parts.Count - 1)]) {
        $pair = $part -split "=", 2
        if ($pair.Count -eq 2) { $values[$pair[0]] = $pair[1] }
    }
    $values.line = $Line
    $values
}

function Register-FieldOwnedProcessesFromInvokeLog() {
    $records = @(Read-InvokeLines $script:FieldInvokeLog |
        Select-Object -Skip $script:FieldInitialInvokeLineCount |
        ForEach-Object { Parse-InvokeLine $_ })
    foreach ($invocationId in @($records | ForEach-Object {
        [string]$_.invocation_id
    } | Where-Object { $_ -match '^[0-9a-f]{32}$' } | Sort-Object -Unique)) {
        $group = @($records | Where-Object {
            [string]$_.invocation_id -eq $invocationId
        })
        $selection = @($group | Where-Object {
            [string]$_.event -eq "selection_succeeded"
        } | Select-Object -First 1)
        $created = @($group | Where-Object {
            [string]$_.event -eq "create_process_succeeded"
        } | Select-Object -First 1)
        if ($selection.Count -ne 1 -or $created.Count -ne 1) { continue }
        if (-not $script:FieldOwnedProjectDigests.ContainsKey(
            [string]$selection[0].project_sha256
        )) { continue }
        $createdProcessId = 0
        if (-not [int]::TryParse([string]$created[0].detail, [ref]$createdProcessId) -or
            $createdProcessId -le 0) { continue }
        Register-FieldOwnedProcess $createdProcessId $invocationId
    }
}

function Wait-NativeSequence(
    [string]$Path,
    [string]$Source,
    [int]$AfterLineCount,
    [string[]]$TerminalEvents = @("child_running")
) {
    $failureEvents = @(
        "selection_failed", "marker_missing", "create_process_failed",
        "child_wait_failed", "child_exit_read_failed", "child_process_failed"
    )
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    $lastObserved = "none"
    do {
        $lines = @(Read-InvokeLines $Path)
        $newRecords = @($lines | Select-Object -Skip $AfterLineCount | ForEach-Object {
            Parse-InvokeLine $_
        })
        $enterRecords = @($newRecords | Where-Object { $_.event -eq "invoke_enter" })
        if ($enterRecords.Count -gt 1) {
            $dispatchIds = @($enterRecords | ForEach-Object { $_.invocation_id }) -join ","
            throw "One UI dispatch produced multiple native invoke_enter records: $dispatchIds"
        }
        if ($enterRecords.Count -eq 1) {
            $enter = $enterRecords[0]
            Assert-Field ([string]$enter.selection_source -eq $Source) `
                ("Native invocation source mismatch: expected=$Source; " +
                 "observed=$($enter.selection_source); invocation_id=$($enter.invocation_id)")
            $id = [string]$enter.invocation_id
            Assert-Field ($id -match '^[0-9a-f]{32}$') `
                "Native invocation has an invalid invocation_id: $id"
            $group = @($newRecords | Where-Object { [string]$_.invocation_id -eq $id })
            $events = @($group | ForEach-Object { [string]$_.event })
            $lastObserved = $events -join ","
            $failure = @($group | Where-Object { $failureEvents -contains $_.event } |
                Select-Object -First 1)
            if ($failure.Count -eq 1) {
                throw ("Native invocation failed: event=$($failure[0].event); " +
                    "detail=$($failure[0].detail); invocation_id=$id")
            }
            if ($events -contains "child_exited") {
                $childExit = @($group | Where-Object { $_.event -eq "child_exited" } |
                    Select-Object -First 1)[0]
                if ([string]$childExit.detail -ne "0") {
                    throw ("Native child exited unsuccessfully: event=child_exited; " +
                        "exit_code=$($childExit.detail); invocation_id=$id")
                }
            }
            $expectedPrefix = @(
                "invoke_enter", "selection_succeeded", "create_process_succeeded"
            )
            $prefixCount = [Math]::Min($events.Count, $expectedPrefix.Count)
            if ($prefixCount -gt 0 -and
                ($events[0..($prefixCount - 1)] -join ",") -ne
                ($expectedPrefix[0..($prefixCount - 1)] -join ",")) {
                throw "Native sequence is malformed: events=$lastObserved; invocation_id=$id"
            }
            if ($events.Count -gt 4) {
                throw "Native sequence contains extra events: events=$lastObserved; invocation_id=$id"
            }
            if ($events.Count -ge 3) {
                $processId = 0
                Assert-Field ([int]::TryParse([string]$group[2].detail, [ref]$processId)) `
                    "Native create_process_succeeded detail is not a process id: $($group[2].detail)"
                Assert-Field ($processId -gt 0) `
                    "Native create_process_succeeded process id is not positive: $processId"
                Register-FieldOwnedProcess $processId $id
            }
            if ($events.Count -eq 4) {
                Assert-Field ($TerminalEvents -contains $events[3]) `
                    ("Native sequence ended with unexpected terminal event=$($events[3]); " +
                     "detail=$($group[3].detail); invocation_id=$id")
                $terminalDetailValid = if ($events[3] -eq "child_running") {
                    $group[3].detail -eq $group[2].detail
                } else { $group[3].detail -eq "0" }
                Assert-Field $terminalDetailValid `
                    ("Native terminal detail is inconsistent: event=$($events[3]); " +
                     "detail=$($group[3].detail); process_id=$processId; invocation_id=$id")
                return @{
                    invocation_id = $id
                    project_sha256 = $group[1].project_sha256
                    process_id = $processId
                    terminal_event = $events[3]
                    lines = @($group | ForEach-Object { $_.line })
                }
            }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    $finalLines = @(Read-InvokeLines $Path)
    throw (
        "Native sequence timed out for $Source; observed_events=$lastObserved; " +
        "after_line_count=$AfterLineCount; final_line_count=$($finalLines.Count)."
    )
}

function Assert-BusyMessageAndClose([int]$ExpectedProcessId) {
    $dialog = Wait-VisibleWindowByPrefix "Skill Magnet エラー" $ExpectedProcessId
    $containsBusy = $false
    $actionable = $false
    $text = Get-VisibleDescendantText $dialog
    if ($text -like "*もう一度*" -or $text -like "*再実行*") {
        $actionable = $true
    }
    foreach ($element in @($dialog.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    ))) {
        try {
            if ($element.Current.Name -like "*別のフォルダー*") { $containsBusy = $true }
        } catch { }
    }
    Assert-Field $containsBusy "Different-folder invocation did not show its recovery message."
    Assert-Field $actionable "Different-folder busy message has no concrete retry action."
    $ok = Get-ButtonCount $dialog "OK"
    Assert-Field ($ok -eq 1) "Busy recovery dialog does not expose one OK button."
    $observation = @{
        element = Get-UiaElementSnapshot $dialog
        busy_text_visible = $containsBusy
        actionable_recovery_visible = $actionable
        ok_button_count = $ok
    }
    Close-UiaWindow $dialog
    $observation
}

$configPath = Assert-FieldRegularPathBoundary $Config
$InvokeEvidence = [IO.Path]::GetFullPath($InvokeEvidence)
$FieldBundle = [IO.Path]::GetFullPath($FieldBundle)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$expectedNativeSource = Get-NativeSourceManifest $repositoryRoot
$script:FieldSessionId = [guid]::NewGuid().ToString("N")
$script:UiaTranscriptSequence = 0
$script:UiaTranscriptLines = [Collections.Generic.List[string]]::new()
$statusJson = python -I -m skill_magnet --config $configPath context-menu-status `
    --platform windows | Out-String
Assert-Field ($LASTEXITCODE -eq 0) "Context-menu status command failed."
$status = $statusJson | ConvertFrom-Json
Assert-Field ([bool]$status.usable_installed_state) "Installed context menu is not usable."
Assert-Field ([int]$status.same_name_package_count -eq 1) "Same-name package count is not one."
Assert-Field ([int]$status.expected_identity_match_count -eq 1) "Expected package count is not one."
Assert-Field ([int]$status.unexpected_same_name_package_count -eq 0) "Foreign same-name package exists."
Assert-Field ([int]$status.menu_leaf_count -eq 0) "Explorer child leaves remain installed."
Assert-Field ([int]$status.menu_action_count -eq 1) "Explorer root action count is not one."
Assert-Field (
    [string]$status.native_source_tree_sha256 -eq
    [string]$expectedNativeSource.source_tree_sha256
) "Installed package native source digest differs from this release."
Assert-Field (
    [bool]$status.native_source_manifest_valid -and
    [bool]$status.native_artifact_hashes_valid -and
    [bool]$status.dll_native_source_binding_valid -and
    [bool]$status.native_build_binding_valid
) "Installed package is not bound to the current native source and artifacts."

$runtimeTreeWalker = @'
import hashlib
import os
import pathlib
import stat
import time

RUNTIME_MAX_ENTRIES = 4096
RUNTIME_MAX_FILE_BYTES = 32 * 1024 * 1024
RUNTIME_MAX_TOTAL_BYTES = 128 * 1024 * 1024
RUNTIME_MAX_SECONDS = 15.0
RUNTIME_READ_CHUNK_BYTES = 1024 * 1024
WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x400


def runtime_failure(label, detail):
    return RuntimeError(
        "runtime safety scan rejected {}: {}. Restore a stable regular-file "
        "installation/checkout, reinstall the same release if needed, and rerun"
        .format(label, detail)
    )


def runtime_is_reparse(path, metadata):
    if stat.S_ISLNK(metadata.st_mode):
        return True
    if int(getattr(metadata, "st_file_attributes", 0)) & WINDOWS_REPARSE_POINT_ATTRIBUTE:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def runtime_identity(metadata, directory):
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
        int(getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1000000000))),
    )
    return identity if directory else identity + (int(metadata.st_size),)


def runtime_entry_fingerprint(metadata, directory):
    fingerprint = (
        int(stat.S_IFMT(metadata.st_mode)),
        int(getattr(metadata, "st_file_attributes", 0)),
        int(getattr(metadata, "st_reparse_tag", 0)),
    )
    return fingerprint if directory else fingerprint + (
        int(getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1000000000))),
        int(metadata.st_size),
    )


def runtime_checked_metadata(path, label, directory, expected=None):
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise runtime_failure(label, "lstat failed ({})".format(type(error).__name__)) from error
    if runtime_is_reparse(path, metadata):
        raise runtime_failure(label, "links, junctions, and reparse points are forbidden")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        raise runtime_failure(label, "expected a {}".format("directory" if directory else "regular file"))
    identity = runtime_identity(metadata, directory)
    if expected is not None and identity != expected:
        raise runtime_failure(label, "identity or metadata changed during verification")
    return metadata, identity


def runtime_check_budget(budget, label):
    if time.monotonic() > budget["deadline"]:
        raise runtime_failure(label, "the bounded scan deadline expired")


def runtime_note_entry(budget, label):
    runtime_check_budget(budget, label)
    budget["entries"] += 1
    if budget["entries"] > RUNTIME_MAX_ENTRIES:
        raise runtime_failure(label, "entry count exceeds {}".format(RUNTIME_MAX_ENTRIES))


def runtime_stable_read(path, label, metadata, identity, parent, parent_identity, budget):
    size = int(metadata.st_size)
    if size > RUNTIME_MAX_FILE_BYTES:
        raise runtime_failure(label, "file size {} exceeds {} bytes".format(size, RUNTIME_MAX_FILE_BYTES))
    if budget["bytes"] + size > RUNTIME_MAX_TOTAL_BYTES:
        raise runtime_failure(label, "total bytes exceed {}".format(RUNTIME_MAX_TOTAL_BYTES))
    runtime_check_budget(budget, label)
    runtime_checked_metadata(parent, "parent of " + label, True, parent_identity)
    runtime_checked_metadata(path, label, False, identity)
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOINHERIT", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        if runtime_identity(os.fstat(descriptor), False) != identity:
            raise runtime_failure(label, "opened file identity differs from lstat")
        runtime_checked_metadata(path, label, False, identity)
        runtime_checked_metadata(parent, "parent of " + label, True, parent_identity)
        chunks = []
        remaining = size
        while remaining:
            runtime_check_budget(budget, label)
            chunk = os.read(descriptor, min(RUNTIME_READ_CHUNK_BYTES, remaining))
            runtime_check_budget(budget, label)
            if not chunk:
                raise runtime_failure(label, "file ended before its verified size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise runtime_failure(label, "file grew while it was being read")
        runtime_check_budget(budget, label)
        payload = b"".join(chunks)
        if len(payload) != size:
            raise runtime_failure(label, "read size differs from verified size")
        if runtime_identity(os.fstat(descriptor), False) != identity:
            raise runtime_failure(label, "opened file changed while it was read")
        runtime_checked_metadata(path, label, False, identity)
        runtime_checked_metadata(parent, "parent of " + label, True, parent_identity)
    except OSError as error:
        raise runtime_failure(label, "bounded read failed ({})".format(type(error).__name__)) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    budget["bytes"] += size
    return payload


def runtime_collect_tree(
    root, prefix, label, budget, include_file, skip_directory, reject_directory=None
):
    _, root_identity = runtime_checked_metadata(root, label, True)
    pending = [(root, pathlib.Path(), root_identity)]
    collected = {}
    while pending:
        directory, relative_directory, expected_directory_identity = pending.pop()
        directory_label = label if not relative_directory.parts else label + "/" + relative_directory.as_posix()
        _, directory_identity = runtime_checked_metadata(
            directory, directory_label, True, expected_directory_identity
        )
        runtime_check_budget(budget, directory_label)
        try:
            scanner = os.scandir(directory)
        except OSError as error:
            raise runtime_failure(
                directory_label, "directory open failed ({})".format(type(error).__name__)
            ) from error
        try:
            runtime_checked_metadata(directory, directory_label, True, directory_identity)
            discovered = []
            for entry in scanner:
                child = directory / entry.name
                relative = relative_directory / entry.name
                child_label = label + "/" + relative.as_posix()
                runtime_note_entry(budget, child_label)
                try:
                    entry_metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise runtime_failure(
                        child_label, "entry stat failed ({})".format(type(error).__name__)
                    ) from error
                if runtime_is_reparse(child, entry_metadata):
                    raise runtime_failure(child_label, "links, junctions, and reparse points are forbidden")
                is_directory = stat.S_ISDIR(entry_metadata.st_mode)
                if not is_directory and not stat.S_ISREG(entry_metadata.st_mode):
                    raise runtime_failure(child_label, "unsupported filesystem entry type")
                current_metadata, current_identity = runtime_checked_metadata(
                    child, child_label, is_directory
                )
                if runtime_entry_fingerprint(
                    entry_metadata, is_directory
                ) != runtime_entry_fingerprint(current_metadata, is_directory):
                    raise runtime_failure(
                        child_label, "entry metadata changed between stat and lstat"
                    )
                runtime_checked_metadata(directory, directory_label, True, directory_identity)
                discovered.append((child, relative, current_metadata, current_identity, is_directory))
        finally:
            scanner.close()
        runtime_check_budget(budget, directory_label)
        runtime_checked_metadata(directory, directory_label, True, directory_identity)
        for child, relative, metadata, identity, is_directory in sorted(
            discovered, key=lambda item: item[1].as_posix()
        ):
            child_label = label + "/" + relative.as_posix()
            if is_directory:
                if reject_directory is not None and reject_directory(relative):
                    raise runtime_failure(
                        child_label, "this generated/residue directory is not allowed here"
                    )
                if not skip_directory(relative):
                    pending.append((child, relative, identity))
                continue
            if not include_file(relative):
                continue
            collected[prefix + relative.as_posix()] = runtime_stable_read(
                child, child_label, metadata, identity, directory, directory_identity, budget
            )
        runtime_checked_metadata(directory, directory_label, True, directory_identity)
    return collected


def runtime_logical_digest(entries):
    digest = hashlib.sha256()
    for name in sorted(entries):
        content = entries[name]
        if b"\0" not in content:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                content = content.replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()
'@

$runtimeProbe = $runtimeTreeWalker + @'

import importlib.metadata
import json
import sys
import skill_magnet

module_path = pathlib.Path(os.path.abspath(skill_magnet.__file__))
root = module_path.parent
distribution = importlib.metadata.distribution("skill-magnet")
distribution_module_paths = [
    pathlib.Path(os.path.abspath(distribution.locate_file(item)))
    for item in (distribution.files or ())
    if item.as_posix() == "skill_magnet/__init__.py"
]
if distribution.metadata.get("Name", "").casefold() != "skill-magnet":
    raise RuntimeError("installed distribution name is not skill-magnet")
if len(distribution_module_paths) != 1 or distribution_module_paths[0] != module_path:
    raise RuntimeError("imported module is not owned by the installed skill-magnet distribution")
budget = {
    "deadline": time.monotonic() + RUNTIME_MAX_SECONDS,
    "entries": 0,
    "bytes": 0,
}
entries = runtime_collect_tree(
    root,
    "skill_magnet/",
    "installed runtime",
    budget,
    lambda relative: relative.suffix.lower() != ".pyc",
    lambda relative: relative.name == "__pycache__",
    lambda relative: relative.as_posix().casefold()
        == "_native/windows-modern-context-menu/out",
)
print(json.dumps({
    "module_version": skill_magnet.__version__,
    "distribution_version": distribution.version,
    "distribution_name": distribution.metadata["Name"].casefold(),
    "executable": sys.executable,
    "module_path": str(module_path),
    "distribution_module_path": str(distribution_module_paths[0]),
    "payload_sha256": runtime_logical_digest(entries),
}))
'@
$runtimeOutput = @($runtimeProbe |
    & ([string]$status.command_target) -I - 2>&1)
$runtimeExitCode = $LASTEXITCODE
$runtimeJson = ($runtimeOutput | Out-String).Trim()
Assert-Field ($runtimeExitCode -eq 0) (
    "Installed runtime safety scan failed before Explorer input. " +
    "Reinstall the exact release wheel, ensure its package tree has no links or build residue, and rerun."
)
$runtime = $runtimeJson | ConvertFrom-Json
$appReleaseVersion = ([string]$status.version) -replace '\.0$', ''
Assert-Field (
    [string]$runtime.module_version -eq $appReleaseVersion -and
    [string]$runtime.distribution_version -eq $appReleaseVersion -and
    [string]$runtime.distribution_name -eq "skill-magnet"
) "Imported module and installed distribution versions do not match the context-menu package."
Assert-Field (
    [IO.Path]::GetFullPath([string]$runtime.module_path) -eq
    [IO.Path]::GetFullPath([string]$runtime.distribution_module_path)
) "Imported module does not belong to the installed skill-magnet distribution."
$repositoryForRuntimeCheck = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$modulePath = [IO.Path]::GetFullPath([string]$runtime.module_path)
Assert-Field (-not $modulePath.StartsWith(
    $repositoryForRuntimeCheck + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
)) "Installed menu Python runtime resolves into the repository checkout."

# Prove the installed Python payload is this checkout's release payload before
# the collector opens Explorer or sends any mouse input.  The final gate repeats
# the comparison independently, but a post-click rejection is too late for a
# physical-input safety boundary.
$releaseRuntimeProbe = $runtimeTreeWalker + @'

import sys

repository = pathlib.Path(os.path.abspath(sys.argv[1]))
_, repository_identity = runtime_checked_metadata(repository, "repository root", True)
budget = {
    "deadline": time.monotonic() + RUNTIME_MAX_SECONDS,
    "entries": 0,
    "bytes": 0,
}
package_source = repository / "src" / "skill_magnet"
native_source = repository / "native" / "windows-modern-context-menu"
blocked_names = {".git", "out", "__pycache__"}
blocked_suffixes = {".obj", ".lib", ".exp", ".pyc"}
entries = runtime_collect_tree(
    package_source,
    "skill_magnet/",
    "package source",
    budget,
    lambda relative: relative.suffix.lower() == ".py",
    lambda relative: relative.name == "__pycache__",
)
entries.update(runtime_collect_tree(
    native_source,
    "skill_magnet/_native/windows-modern-context-menu/",
    "native source",
    budget,
    lambda relative: relative.suffix.lower() not in blocked_suffixes,
    lambda relative: relative.name in blocked_names,
))
config = repository / "skill-magnet.json"
runtime_note_entry(budget, "release config")
config_metadata, config_identity = runtime_checked_metadata(config, "release config", False)
entries["skill_magnet/skill-magnet.json"] = runtime_stable_read(
    config,
    "release config",
    config_metadata,
    config_identity,
    repository,
    repository_identity,
    budget,
)
runtime_checked_metadata(repository, "repository root", True, repository_identity)
print(runtime_logical_digest(entries))
'@
$releaseRuntimeOutput = @($releaseRuntimeProbe |
    & ([string]$status.command_target) -I - $repositoryRoot 2>&1)
$releaseRuntimeExitCode = $LASTEXITCODE
$releaseRuntimeDigest = ($releaseRuntimeOutput | Out-String).Trim()
Assert-Field (
    $releaseRuntimeExitCode -eq 0 -and
    $releaseRuntimeDigest -match '^[0-9a-f]{64}$' -and
    [string]$runtime.payload_sha256 -ceq $releaseRuntimeDigest
) (
    "Installed payload or release-input safety verification failed; no Explorer input was sent. " +
    "Restore a stable regular-file checkout, reinstall the exact release wheel, and rerun."
)

$selectionProbe = @'
import hashlib
import json
import pathlib
import sys

from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config
from skill_magnet.library_ui import configured_repository_url
from skill_magnet.ui import context_selection_choice_map

config_path = pathlib.Path(sys.argv[1]).resolve()
engine = ActivationEngine(Config.load(config_path))
choices = [
    {"label": label, "pack_id": value[0], "skill_id": value[1]}
    for label, value in context_selection_choice_map(engine).items()
]
canonical = json.dumps(
    choices, ensure_ascii=False, separators=(",", ":")
).encode("utf-8")
print(json.dumps({
    "choices": choices,
    "choice_map_sha256": hashlib.sha256(canonical).hexdigest(),
    "configured_remote": configured_repository_url(config_path),
}, ensure_ascii=True))
'@
$selectionJson = $selectionProbe |
    & ([string]$status.command_target) -I - $configPath | Out-String
Assert-Field ($LASTEXITCODE -eq 0) "Installed selector-contract probe failed."
$selectionContract = $selectionJson | ConvertFrom-Json
$expectedChoices = @($selectionContract.choices)
Assert-Field ($expectedChoices.Count -gt 0) "Configured selector has no choices."
Assert-Field ([string]$selectionContract.choice_map_sha256 -match '^[0-9a-f]{64}$') `
    "Configured selector mapping has no canonical digest."
$configuredRemote = [string]$selectionContract.configured_remote
Assert-Field ($configuredRemote -match '^https://github\.com/[^/]+/[^/]+(?:\.git)?$') `
    "Release config does not identify one GitHub repository for Library Manager."

$invokeLog = Assert-FieldRegularPathBoundary `
    (Join-Path $env:LOCALAPPDATA "SkillMagnet\ContextMenu\invoke.log") $true
$initialLineCount = @(Read-InvokeLines $invokeLog).Count
$testRoot = Join-Path $env:TEMP ("SkillMagnet-Explorer-Field-" + [guid]::NewGuid())
$selectedParent = Join-Path $testRoot "selected parent"
$selectedFolder = Join-Path $selectedParent "selected folder 日本語"
$backgroundFolder = Join-Path $testRoot "background folder 日本語"
$differentFolder = Join-Path $testRoot "different folder"
$managerBusyFolder = Join-Path $testRoot "manager busy folder"
$stateRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".skill-magnet"
$runtimeSkillFolder = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex\skills\cma-004"
$script:FieldExpectedExecutablePath = [IO.Path]::GetFullPath([string]$status.command_target)
$script:FieldContextOwnerPath = Join-Path $stateRoot "context-launcher.owner.json"
$script:FieldInvokeLog = $invokeLog
$script:FieldInitialInvokeLineCount = $initialLineCount
$script:FieldOwnedProjectDigests = @{}
$script:FieldOwnedProcessIdentities = @{}
$script:FieldOwnedOwnerTokens = @{}
$script:FieldPreExistingProcessIdentities = @{}
$script:FieldPreExistingOwnerGeneration = ""
$windows = @()
try {
    [IO.Directory]::CreateDirectory($selectedFolder) | Out-Null
    [IO.Directory]::CreateDirectory($backgroundFolder) | Out-Null
    [IO.Directory]::CreateDirectory($differentFolder) | Out-Null
    [IO.Directory]::CreateDirectory($managerBusyFolder) | Out-Null
    Assert-Field (Test-Path -LiteralPath $runtimeSkillFolder -PathType Container) `
        "Read-only runtime-skill field folder is missing: cma-004"
    foreach ($fieldPath in @(
        $selectedFolder, $backgroundFolder, $differentFolder,
        $managerBusyFolder, $runtimeSkillFolder
    )) {
        $script:FieldOwnedProjectDigests[(Get-Utf16Sha256 $fieldPath)] = $true
    }
    foreach ($process in @(Get-Process)) {
        $identity = Get-FieldProcessIdentity ([int]$process.Id)
        if ($null -ne $identity) {
            $script:FieldPreExistingProcessIdentities[[string]$process.Id] = $identity
        }
    }
    $initialOwner = Read-FieldContextOwner
    $script:FieldPreExistingOwnerGeneration = if ($null -ne $initialOwner) {
        [string]$initialOwner.generation
    } else { "" }

    $selectedWindow = Open-ExplorerFolder $selectedParent
    $windows += $selectedWindow
    $before = @(Read-InvokeLines $invokeLog).Count
    $selectedMenu = Invoke-VisibleSkillMagnetRoot `
        $selectedWindow (Split-Path $selectedFolder -Leaf) "selected_item"
    $selectedSequence = Wait-NativeSequence $invokeLog "selected_item" $before
    $selectedGui = Inspect-UnifiedGui `
        $selectedFolder $expectedChoices $selectedSequence.process_id "selected_item"
    Assert-Field ($selectedSequence.project_sha256 -eq (Get-Utf16Sha256 $selectedFolder)) `
        "Selected-folder native digest does not match the Explorer path."
    Assert-Field ([int]$selectedGui.element.Current.ProcessId -eq $selectedSequence.process_id) `
        "Selected-folder GUI does not belong to the native child process."
    Add-UiaTranscriptEvent "native_sequence_bound" "selected_item" ([ordered]@{
        invocation_id = $selectedSequence.invocation_id
        project_sha256 = $selectedSequence.project_sha256
        native_sequence_sha256 = Get-NativeSequenceSha256 $selectedSequence
    })

    # Follow the real root -> unified UI -> Library Manager route.  Merely
    # counting its button is insufficient: the configured remote and every CRUD
    # control must be visible, and closing without a mutation must leave all
    # persistent product surfaces byte-equivalent.
    $managerStateBefore = Get-PersistentMutationSnapshot $configPath $stateRoot
    $selectedManagerClick = Invoke-FieldUiSurfaceWidget `
        $selectedSequence.process_id "context_selection" $selectedGui.element `
        $selectedGui.ui_surface_generation "library_manager" `
        (Get-FieldTargetSha256 $selectedFolder) `
        "library_manager" "Library Manager" | Out-Null
    $managerGui = Inspect-LibraryManager $configuredRemote $selectedSequence.process_id
    $managerSnapshot = $managerGui.element_snapshot

    # While Manager owns the original context process, repeat the same Explorer
    # invocation.  It must focus the Manager window, not a destroyed selector HWND.
    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $selectedWindow (Split-Path $selectedFolder -Leaf) | Out-Null
    $managerSameSequence = Wait-NativeSequence `
        $invokeLog "selected_item" $before @("child_running", "child_exited")
    Start-Sleep -Seconds 2
    $managerFieldProcessIds = @(
        [int]$selectedSequence.process_id, [int]$managerSameSequence.process_id
    ) | Sort-Object -Unique
    $visibleManagers = @(Get-VisibleWindowsByPrefix "Library Manager" | Where-Object {
        try { $managerFieldProcessIds -contains [int]$_.Current.ProcessId }
        catch { $false }
    })
    Assert-Field ($visibleManagers.Count -eq 1) `
        "Same-folder click while Manager is open created or lost a Manager window."
    $managerForeground = [SkillMagnetFieldInput]::GetForegroundWindow()
    $managerHandle = [IntPtr]([int64]$managerGui.element.Current.NativeWindowHandle)
    Assert-Field ($managerForeground -eq $managerHandle) `
        "Same-folder click did not focus the existing Library Manager."
    $managerSameErrors = @(Get-VisibleNamedElements "Skill Magnet エラー" |
        Where-Object {
            try { $managerFieldProcessIds -contains [int]$_.Current.ProcessId }
            catch { $false }
        })
    Assert-Field ($managerSameErrors.Count -eq 0) `
        "Same-folder click displayed an error while Library Manager was open."

    # A different folder must remain fail-closed but recoverable while Manager is open.
    $managerBusyWindow = Open-ExplorerFolder $managerBusyFolder
    $windows += $managerBusyWindow
    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $managerBusyWindow | Out-Null
    $managerDifferentSequence = Wait-NativeSequence $invokeLog "background_site" $before
    Assert-Field (
        $managerDifferentSequence.project_sha256 -eq (Get-Utf16Sha256 $managerBusyFolder)
    ) "Manager-busy invocation did not bind the attempted folder."
    $managerBusyObservation = Assert-BusyMessageAndClose $managerDifferentSequence.process_id
    Assert-Field (
        [int]$managerBusyObservation.element.process_id -eq $managerDifferentSequence.process_id
    ) "Manager-busy dialog does not belong to the duplicate launcher process."

    Close-UiaWindow $managerGui.element
    Wait-VisibleWindowClosed $selectedSequence.process_id "Library Manager"
    Wait-ProcessExited $selectedSequence.process_id
    $managerStateAfter = Get-PersistentMutationSnapshot $configPath $stateRoot
    $managerNoMutation = Test-SnapshotEqual $managerStateBefore $managerStateAfter
    Assert-Field $managerNoMutation `
        "Opening and closing Library Manager changed config, library, or transaction state."
    Add-UiaTranscriptEvent "library_manager_flow_observed" "library_manager_flow" ([ordered]@{
        element = $managerSnapshot
        configured_remote_sha256 = $managerGui.configured_remote_sha256
        configured_remote_visible = $managerGui.configured_remote_visible
        create_button_count = $managerGui.create_button_count
        create_button_text_sha256 = $managerGui.create_button_text_sha256
        update_button_count = $managerGui.update_button_count
        update_button_text_sha256 = $managerGui.update_button_text_sha256
        delete_button_count = $managerGui.delete_button_count
        delete_button_text_sha256 = $managerGui.delete_button_text_sha256
        reload_button_count = $managerGui.reload_button_count
        reload_button_text_sha256 = $managerGui.reload_button_text_sha256
        same_folder_repeat_invocation_id = $managerSameSequence.invocation_id
        same_folder_repeat_project_sha256 = $managerSameSequence.project_sha256
        same_folder_repeat_native_sequence_sha256 = Get-NativeSequenceSha256 $managerSameSequence
        same_folder_repeat_focused_existing_manager = $true
        same_folder_repeat_manager_count = $visibleManagers.Count
        same_folder_repeat_error_count = $managerSameErrors.Count
        different_folder_invocation_id = $managerDifferentSequence.invocation_id
        different_folder_project_sha256 = $managerDifferentSequence.project_sha256
        different_folder_native_sequence_sha256 = Get-NativeSequenceSha256 $managerDifferentSequence
        different_folder_busy_element = $managerBusyObservation.element
        different_folder_busy_text_visible = $managerBusyObservation.busy_text_visible
        different_folder_actionable_recovery_visible = $managerBusyObservation.actionable_recovery_visible
        different_folder_ok_button_count = $managerBusyObservation.ok_button_count
        state_before = $managerStateBefore
        state_after = $managerStateAfter
        no_persistent_mutation = $managerNoMutation
    })

    $backgroundWindow = Open-ExplorerFolder $backgroundFolder
    $windows += $backgroundWindow
    $before = @(Read-InvokeLines $invokeLog).Count
    $backgroundMenu = Invoke-VisibleSkillMagnetRoot $backgroundWindow "" "background_site"
    $backgroundSequence = Wait-NativeSequence $invokeLog "background_site" $before
    $backgroundGui = Inspect-UnifiedGui `
        $backgroundFolder $expectedChoices $backgroundSequence.process_id "background_site"
    Assert-Field ($backgroundSequence.project_sha256 -eq (Get-Utf16Sha256 $backgroundFolder)) `
        "Background-folder native digest does not match the Explorer path."
    Assert-Field ([int]$backgroundGui.element.Current.ProcessId -eq $backgroundSequence.process_id) `
        "Background-folder GUI does not belong to the native child process."
    $backgroundGuiSnapshot = Get-UiaElementSnapshot $backgroundGui.element
    Add-UiaTranscriptEvent "native_sequence_bound" "background_site" ([ordered]@{
        invocation_id = $backgroundSequence.invocation_id
        project_sha256 = $backgroundSequence.project_sha256
        native_sequence_sha256 = Get-NativeSequenceSha256 $backgroundSequence
    })

    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $backgroundWindow | Out-Null
    $sameSequence = Wait-NativeSequence `
        $invokeLog "background_site" $before @("child_running", "child_exited")
    Start-Sleep -Seconds 2
    $sameFieldProcessIds = @(
        [int]$backgroundSequence.process_id, [int]$sameSequence.process_id
    ) | Sort-Object -Unique
    $sameGuis = @(Get-VisibleNamedElements "Skill Magnet — 実行確認" |
        Where-Object {
            try { $sameFieldProcessIds -contains [int]$_.Current.ProcessId }
            catch { $false }
        })
    $sameGuiCount = $sameGuis.Count
    Assert-Field ($sameGuiCount -eq 1) "Repeated same-folder click created another GUI."
    $focusedWindow = [SkillMagnetFieldInput]::GetForegroundWindow()
    $expectedFocusedWindow = [IntPtr]([int64]$backgroundGui.element.Current.NativeWindowHandle)
    Assert-Field ($focusedWindow -eq $expectedFocusedWindow) `
        "Repeated same-folder click did not focus the existing Skill Magnet GUI."
    $unexpectedErrors = @(Get-VisibleNamedElements "Skill Magnet エラー" |
        Where-Object {
            try { $sameFieldProcessIds -contains [int]$_.Current.ProcessId }
            catch { $false }
        })
    Assert-Field ($unexpectedErrors.Count -eq 0) `
        "Repeated same-folder click displayed an error instead of focusing the existing GUI."
    Assert-Field ($sameSequence.project_sha256 -eq $backgroundSequence.project_sha256) `
        "Repeated same-folder click did not preserve the folder digest."
    Assert-Field ($sameSequence.invocation_id -ne $backgroundSequence.invocation_id) `
        "Repeated same-folder click did not create a distinct native invocation."
    Assert-Field ($sameSequence.process_id -ne $backgroundSequence.process_id) `
        "Repeated same-folder click did not run in a separate launcher process."
    $sameGuiSnapshot = Get-UiaElementSnapshot $sameGuis[0]
    Add-UiaTranscriptEvent "same_folder_repeat_observed" "same_folder_repeat" ([ordered]@{
        element = $sameGuiSnapshot
        project_sha256 = $sameSequence.project_sha256
        original_invocation_id = $backgroundSequence.invocation_id
        repeat_invocation_id = $sameSequence.invocation_id
        repeat_native_sequence_sha256 = Get-NativeSequenceSha256 $sameSequence
        gui_count = $sameGuiCount
        foreground_window_handle = $focusedWindow.ToInt64()
        unexpected_error_count = $unexpectedErrors.Count
    })

    $differentWindow = Open-ExplorerFolder $differentFolder
    $windows += $differentWindow
    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $differentWindow | Out-Null
    $differentSequence = Wait-NativeSequence $invokeLog "background_site" $before
    Assert-Field ($differentSequence.project_sha256 -eq (Get-Utf16Sha256 $differentFolder)) `
        "Different-folder busy invocation did not bind the attempted folder."
    $busyObservation = Assert-BusyMessageAndClose $differentSequence.process_id
    Assert-Field (
        [int]$busyObservation.element.process_id -eq $differentSequence.process_id
    ) "Different-folder busy dialog does not belong to the native child process."
    Add-UiaTranscriptEvent "different_folder_busy_observed" "different_folder_busy" ([ordered]@{
        element = $busyObservation.element
        project_sha256 = $differentSequence.project_sha256
        invocation_id = $differentSequence.invocation_id
        native_sequence_sha256 = Get-NativeSequenceSha256 $differentSequence
        busy_text_visible = $busyObservation.busy_text_visible
        actionable_recovery_visible = $busyObservation.actionable_recovery_visible
        ok_button_count = $busyObservation.ok_button_count
    })

    $backgroundProcessId = [int]$backgroundGui.element.Current.ProcessId
    Close-UiaWindow $backgroundGui.element
    Wait-ProcessExited $backgroundProcessId
    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $backgroundWindow | Out-Null
    $relaunchSequence = Wait-NativeSequence $invokeLog "background_site" $before
    $relaunched = Inspect-UnifiedGui `
        $backgroundFolder $expectedChoices $relaunchSequence.process_id
    Assert-Field ([int]$relaunched.element.Current.ProcessId -eq $relaunchSequence.process_id) `
        "Relaunched GUI does not belong to the new native child process."
    Assert-Field ($relaunchSequence.process_id -ne $backgroundProcessId) `
        "Closed-window relaunch reused the stopped GUI process."
    Assert-Field ($relaunchSequence.invocation_id -ne $backgroundSequence.invocation_id) `
        "Closed-window relaunch did not create a distinct native invocation."
    Assert-Field ($relaunchSequence.project_sha256 -eq $backgroundSequence.project_sha256) `
        "Closed-window relaunch did not preserve the folder digest."
    $relaunchSnapshot = Get-UiaElementSnapshot $relaunched.element
    Add-UiaTranscriptEvent "closed_window_relaunch_observed" "closed_window_relaunch" ([ordered]@{
        element = $relaunchSnapshot
        project_sha256 = $relaunchSequence.project_sha256
        original_invocation_id = $backgroundSequence.invocation_id
        relaunch_invocation_id = $relaunchSequence.invocation_id
        relaunch_native_sequence_sha256 = Get-NativeSequenceSha256 $relaunchSequence
        original_process_id = $backgroundGuiSnapshot.process_id
        original_native_window_handle = $backgroundGuiSnapshot.native_window_handle
        original_runtime_id = $backgroundGuiSnapshot.runtime_id
    })
    Close-UiaWindow $relaunched.element
    Wait-ProcessExited $relaunchSequence.process_id

    # Exercise the explicit registration route against an application-created,
    # empty folder.  The selected path must be carried into Manager, validation
    # must name the missing SKILL.md, and the user must receive a concrete retry
    # action without any local CRUD/config transaction being committed.
    $registrationStateBefore = Get-PersistentMutationSnapshot $configPath $stateRoot
    $before = @(Read-InvokeLines $invokeLog).Count
    $registrationMenu = Invoke-VisibleSkillMagnetRoot `
        $selectedWindow (Split-Path $selectedFolder -Leaf)
    $registrationSequence = Wait-NativeSequence $invokeLog "selected_item" $before
    $registrationGui = Inspect-UnifiedGui `
        $selectedFolder $expectedChoices $registrationSequence.process_id
    Assert-Field (
        $registrationSequence.project_sha256 -eq (Get-Utf16Sha256 $selectedFolder)
    ) "Registration invocation did not bind the selected empty folder."
    Assert-Field (
        [int]$registrationGui.element.Current.ProcessId -eq $registrationSequence.process_id
    ) "Registration selector does not belong to the native child process."
    $registrationGuiSnapshot = Get-UiaElementSnapshot $registrationGui.element
    $registrationClick = Invoke-FieldUiSurfaceWidget `
        $registrationSequence.process_id "context_selection" $registrationGui.element `
        $registrationGui.ui_surface_generation "register_selected" `
        (Get-FieldTargetSha256 $selectedFolder) `
        "library_manager" "Library Manager" | Out-Null
    $registrationManager = Inspect-LibraryManager `
        $configuredRemote $registrationSequence.process_id
    $selectedPath = [IO.Path]::GetFullPath($selectedFolder)
    $selectedPathMatches = if (
        [string]$registrationManager.registration_source_sha256 -ceq
            (Get-Utf8Sha256 $selectedPath)
    ) { 1 } else { 0 }
    Assert-Field ($selectedPathMatches -eq 1) `
        "Registration Manager did not carry the Explorer-selected-folder digest exactly once."
    $missingSkillDialog = Wait-MissingSkillRecoveryDialog $registrationSequence.process_id
    Close-UiaWindow $missingSkillDialog.element
    Close-UiaWindow $registrationManager.element
    Wait-VisibleWindowClosed $registrationSequence.process_id "Library Manager"
    Wait-ProcessExited $registrationSequence.process_id
    $registrationStateAfter = Get-PersistentMutationSnapshot $configPath $stateRoot
    $registrationNoMutation = Test-SnapshotEqual `
        $registrationStateBefore $registrationStateAfter
    Assert-Field $registrationNoMutation `
        "Rejected empty-folder registration changed config, library, or transaction state."
    Add-UiaTranscriptEvent `
        "missing_skill_registration_observed" `
        "missing_skill_registration" `
        ([ordered]@{
            root_element = $registrationMenu.element
            root_visible_count = $registrationMenu.root_visible_count
            invoke_pattern_available = $registrationMenu.invoke_pattern_available
            expand_collapse_pattern_available = $registrationMenu.expand_collapse_pattern_available
            submenu_item_count = $registrationMenu.submenu_item_count
            unified_element = $registrationGuiSnapshot
            manager_element = $registrationManager.element_snapshot
            error_element = $missingSkillDialog.element_snapshot
            invocation_id = $registrationSequence.invocation_id
            project_sha256 = $registrationSequence.project_sha256
            native_sequence_sha256 = Get-NativeSequenceSha256 $registrationSequence
            selected_path_sha256 = Get-Utf16Sha256 $selectedFolder
            selected_path_visible = ($selectedPathMatches -eq 1)
            missing_skill_cause_visible = $missingSkillDialog.missing_skill_cause_visible
            actionable_recovery_visible = $missingSkillDialog.actionable_recovery_visible
            ok_button_count = $missingSkillDialog.ok_button_count
            state_before = $registrationStateBefore
            state_after = $registrationStateAfter
            no_persistent_mutation = $registrationNoMutation
        })

    # Regression for the user's actual runtime-skill directory.  Right-clicking
    # it is a valid selection signal but never authorizes it as a task workspace.
    # The collector reads and hashes it; it never creates or modifies content there.
    $runtimeParent = Split-Path -Parent $runtimeSkillFolder
    $runtimeWindow = Open-ExplorerFolder $runtimeParent
    $windows += $runtimeWindow
    $runtimeFolderBefore = Get-TreeContentSha256 $runtimeSkillFolder
    $runtimeStateBefore = Get-PersistentMutationSnapshot $configPath $stateRoot
    $before = @(Read-InvokeLines $invokeLog).Count
    $runtimeMenu = Invoke-VisibleSkillMagnetRoot `
        $runtimeWindow (Split-Path $runtimeSkillFolder -Leaf)
    $runtimeSequence = Wait-NativeSequence $invokeLog "selected_item" $before
    $runtimeGui = Inspect-UnifiedGui `
        $runtimeSkillFolder $expectedChoices $runtimeSequence.process_id "" $true
    Assert-Field (
        $runtimeSequence.project_sha256 -eq (Get-Utf16Sha256 $runtimeSkillFolder)
    ) "Runtime-skill native digest does not bind the clicked folder."
    Assert-Field (
        [int]$runtimeGui.element.Current.ProcessId -eq $runtimeSequence.process_id
    ) "Runtime-skill GUI does not belong to the native child process."
    $runtimeProjectWidget = Get-FieldUiSurfaceWidget `
        $runtimeGui.ui_surface "project" "label"
    $runtimeExpectedText =
        "作業対象フォルダー: 指定なし（デスクトップアプリが新規タスク用領域を自動作成）"
    $runtimePathHidden = -not ($runtimeProjectWidget.PSObject.Properties.Name -contains "text")
    $projectlessVisible = (
        [string]$runtimeProjectWidget.text_sha256 -ceq
            (Get-Utf8Sha256 $runtimeExpectedText)
    )
    Assert-Field $runtimePathHidden `
        "Runtime skill folder was incorrectly presented as the task workspace."
    Assert-Field $projectlessVisible `
        "Runtime skill folder did not show explicit projectless Desktop-task semantics."
    $runtimeGuiSnapshot = Get-UiaElementSnapshot $runtimeGui.element
    Close-UiaWindow $runtimeGui.element
    Wait-ProcessExited $runtimeSequence.process_id
    $runtimeFolderAfter = Get-TreeContentSha256 $runtimeSkillFolder
    $runtimeStateAfter = Get-PersistentMutationSnapshot $configPath $stateRoot
    $runtimeReadOnly = (
        $runtimeFolderBefore -eq $runtimeFolderAfter -and
        (Test-SnapshotEqual $runtimeStateBefore $runtimeStateAfter)
    )
    Assert-Field $runtimeReadOnly `
        "Read-only runtime-skill launch changed skill content or persistent product state."
    Add-UiaTranscriptEvent `
        "runtime_skill_projectless_observed" `
        "runtime_skill_projectless" `
        ([ordered]@{
            root_element = $runtimeMenu.element
            root_visible_count = $runtimeMenu.root_visible_count
            invoke_pattern_available = $runtimeMenu.invoke_pattern_available
            expand_collapse_pattern_available = $runtimeMenu.expand_collapse_pattern_available
            submenu_item_count = $runtimeMenu.submenu_item_count
            unified_element = $runtimeGuiSnapshot
            invocation_id = $runtimeSequence.invocation_id
            project_sha256 = $runtimeSequence.project_sha256
            native_sequence_sha256 = Get-NativeSequenceSha256 $runtimeSequence
            clicked_path_sha256 = Get-Utf16Sha256 $runtimeSkillFolder
            runtime_path_hidden_as_workspace = $runtimePathHidden
            projectless_semantics_visible = $projectlessVisible
            skill_content_before_sha256 = $runtimeFolderBefore
            skill_content_after_sha256 = $runtimeFolderAfter
            state_before = $runtimeStateBefore
            state_after = $runtimeStateAfter
            no_persistent_mutation = $runtimeReadOnly
            read_only = $runtimeReadOnly
        })

    $evidenceLines = @(
        $selectedSequence.lines +
        $managerSameSequence.lines +
        $managerDifferentSequence.lines +
        $backgroundSequence.lines +
        $sameSequence.lines +
        $differentSequence.lines +
        $relaunchSequence.lines +
        $registrationSequence.lines +
        $runtimeSequence.lines
    )
    $invokeBytes = [Text.Encoding]::Unicode.GetBytes(
        (($evidenceLines -join "`r`n") + "`r`n")
    )
    [IO.Directory]::CreateDirectory((Split-Path -Parent $InvokeEvidence)) | Out-Null
    $InvokeEvidence = Assert-FieldRegularPathBoundary $InvokeEvidence $true
    [IO.File]::WriteAllBytes($InvokeEvidence, $invokeBytes)

    Assert-Field ($script:UiaTranscriptLines.Count -eq 14) `
        "Raw UIAutomation transcript did not contain the complete 14-event field workflow."
    $transcriptBytes = [Text.UTF8Encoding]::new($false).GetBytes(
        (($script:UiaTranscriptLines -join "`n") + "`n")
    )
    $transcriptDigest = Get-BytesSha256 $transcriptBytes

    $packageRoot = [string]$status.package_content_location
    $externalRoot = [string]$status.external_location
    $appxPath = Join-Path $packageRoot "AppxManifest.xml"
    $menuPath = Join-Path $packageRoot "SkillMagnetMenu.tsv"
    $dllPath = Join-Path $packageRoot "SkillMagnetCommand.dll"
    $identityPath = Join-Path $packageRoot "SkillMagnetIdentity.exe"
    $nativeManifestPath = Join-Path $packageRoot "SkillMagnetNativeSource.json"
    $externalDllPath = Join-Path $externalRoot "SkillMagnetCommand.dll"
    $externalIdentityPath = Join-Path $externalRoot "SkillMagnetIdentity.exe"
    $externalNativeManifestPath = Join-Path $externalRoot "SkillMagnetNativeSource.json"
    $signedMsixPath = Join-Path $externalRoot "SkillMagnet.ContextMenu.msix"

    $nativeProbe = @'
import ctypes, json, pathlib, re, sys
result = {}
for label, raw in (("package", sys.argv[1]), ("external", sys.argv[2])):
    path = pathlib.Path(raw)
    library = ctypes.WinDLL(str(path))
    export = library.SkillMagnetNativeSourceSha256
    export.argtypes = ()
    export.restype = ctypes.c_wchar_p
    digest = export()
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError(label + " DLL export is invalid")
    marker = ("skill-magnet-native-source-v1:" + digest).encode("utf-16-le")
    result[label + "_export"] = digest
    result[label + "_marker_count"] = path.read_bytes().count(marker)
print(json.dumps(result, separators=(",", ":")))
'@
    $nativeProbeJson = $nativeProbe |
        & ([string]$status.command_target) -I - `
            $dllPath $externalDllPath | Out-String
    Assert-Field ($LASTEXITCODE -eq 0) "Installed DLL native-source export probe failed."
    $nativeProbeResult = $nativeProbeJson | ConvertFrom-Json
    Assert-Field (
        [string]$nativeProbeResult.package_export -eq
            [string]$expectedNativeSource.source_tree_sha256 -and
        [string]$nativeProbeResult.external_export -eq
            [string]$expectedNativeSource.source_tree_sha256 -and
        [int]$nativeProbeResult.package_marker_count -eq 1 -and
        [int]$nativeProbeResult.external_marker_count -eq 1
    ) "Package/external DLL does not expose exactly one current native-source binding."

    $contractProbeRoot = Join-Path $testRoot "native contract probe"
    [IO.Directory]::CreateDirectory($contractProbeRoot) | Out-Null
    foreach ($name in @(
        "SkillMagnetCommand.dll", "SkillMagnetIdentity.exe",
        "SkillMagnetNativeSource.json", "SkillMagnetMenu.tsv"
    )) {
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) `
            -Destination (Join-Path $contractProbeRoot $name)
    }
    $contractProbeLines = @(
        & ([string]$status.command_target) -I `
            (Join-Path $repositoryRoot "native\windows-modern-context-menu\contract_test.py") `
            (Join-Path $contractProbeRoot "SkillMagnetCommand.dll") `
            --invoke $contractProbeRoot 2>&1 |
            ForEach-Object { [string]$_ }
    )
    Assert-Field ($LASTEXITCODE -eq 0) "Isolated installed native contract probe failed."
    Assert-Field (
        $contractProbeLines.Count -eq 1 -and
        $contractProbeLines[0] -eq
            "SkillMagnet direct-root IExplorerCommand contract PASS (Python host)"
    ) "Isolated installed native contract probe returned unexpected output."
    $contractProbeBytes = [Text.UTF8Encoding]::new($false).GetBytes(
        $contractProbeLines[0] + "`n"
    )
    $artifacts = [ordered]@{
        appx_manifest = New-ArtifactSnapshot `
            "installed_package" "AppxManifest.xml" $appxPath
        menu_manifest = New-ArtifactSnapshot `
            "installed_package" "SkillMagnetMenu.tsv" $menuPath
        command_dll = New-ArtifactSnapshot `
            "installed_package" "SkillMagnetCommand.dll" $dllPath
        identity_exe = New-ArtifactSnapshot `
            "installed_package" "SkillMagnetIdentity.exe" $identityPath
        native_source_manifest = New-ArtifactSnapshot `
            "installed_package" "SkillMagnetNativeSource.json" $nativeManifestPath
        external_command_dll = New-ArtifactSnapshot `
            "external_install_root" "SkillMagnetCommand.dll" $externalDllPath
        external_identity_exe = New-ArtifactSnapshot `
            "external_install_root" "SkillMagnetIdentity.exe" $externalIdentityPath
        external_native_source_manifest = New-ArtifactSnapshot `
            "external_install_root" "SkillMagnetNativeSource.json" `
            $externalNativeManifestPath
        signed_msix = New-ArtifactSnapshot `
            "external_install_root" "SkillMagnet.ContextMenu.msix" $signedMsixPath
        contract_probe_output = [ordered]@{
            source = "isolated_package_artifact_probe"
            file_name = "contract-test-output.txt"
            size = $contractProbeBytes.Length
            sha256 = Get-BytesSha256 $contractProbeBytes
            bytes_base64 = [Convert]::ToBase64String($contractProbeBytes)
        }
        config = New-HashedArtifactSnapshot `
            "collector_config_argument" "skill-magnet.json" $configPath
    }
    $packageExternalArtifactsEqual = (
        $artifacts.command_dll.sha256 -eq $artifacts.external_command_dll.sha256 -and
        $artifacts.identity_exe.sha256 -eq $artifacts.external_identity_exe.sha256 -and
        $artifacts.native_source_manifest.sha256 -eq
            $artifacts.external_native_source_manifest.sha256
    )
    Assert-Field $packageExternalArtifactsEqual `
        "Package and external native artifacts are not identical."
    $packageNativeManifest = [Text.UTF8Encoding]::new($false, $true).GetString(
        [Convert]::FromBase64String($artifacts.native_source_manifest.bytes_base64)
    ) | ConvertFrom-Json
    $externalNativeManifest = [Text.UTF8Encoding]::new($false, $true).GetString(
        [Convert]::FromBase64String($artifacts.external_native_source_manifest.bytes_base64)
    ) | ConvertFrom-Json
    foreach ($manifest in @($packageNativeManifest, $externalNativeManifest)) {
        Assert-Field (
            [string]$manifest.contract -eq "skill-magnet-native-source-v1" -and
            [string]$manifest.source_tree_sha256 -eq
                [string]$expectedNativeSource.source_tree_sha256 -and
            @($manifest.inputs).Count -eq 12 -and @($manifest.artifacts).Count -eq 2
        ) "Native source manifest does not bind the current 12 inputs and 2 artifacts."
    }
    Assert-Field (
        [string]$packageNativeManifest.artifacts[0].sha256 -eq
            [string]$artifacts.command_dll.sha256 -and
        [long]$packageNativeManifest.artifacts[0].size -eq
            [long]$artifacts.command_dll.size -and
        [string]$packageNativeManifest.artifacts[1].sha256 -eq
            [string]$artifacts.identity_exe.sha256 -and
        [long]$packageNativeManifest.artifacts[1].size -eq
            [long]$artifacts.identity_exe.size
    ) "Native source manifest artifact records do not bind package DLL/Identity bytes."

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($signedMsixPath)
    try {
        $msixPairs = @(
            @("AppxManifest.xml", "appx_manifest"),
            @("SkillMagnetMenu.tsv", "menu_manifest"),
            @("SkillMagnetCommand.dll", "command_dll"),
            @("SkillMagnetIdentity.exe", "identity_exe"),
            @("SkillMagnetNativeSource.json", "native_source_manifest")
        )
        $signedMsixPayloadMatchesPackage = $true
        foreach ($pair in $msixPairs) {
            $entryBytes = Get-ZipEntryBytes $archive ([string]$pair[0])
            $artifactKey = [string]$pair[1]
            $expectedEntryHash = [string]$artifacts[$artifactKey].sha256
            if ((Get-BytesSha256 $entryBytes) -ne $expectedEntryHash) {
                $signedMsixPayloadMatchesPackage = $false
            }
        }
        $signatureBytes = Get-ZipEntryBytes $archive "AppxSignature.p7x"
        if ($signatureBytes.Length -eq 0) { $signedMsixPayloadMatchesPackage = $false }
    }
    finally { $archive.Dispose() }
    Assert-Field $signedMsixPayloadMatchesPackage `
        "Signed MSIX payload does not match the registered package artifacts."
    $hashes = [ordered]@{
        appx_manifest_sha256 = $artifacts.appx_manifest.sha256
        menu_manifest_sha256 = $artifacts.menu_manifest.sha256
        dll_sha256 = $artifacts.command_dll.sha256
        identity_sha256 = $artifacts.identity_exe.sha256
        native_source_manifest_sha256 = $artifacts.native_source_manifest.sha256
        external_dll_sha256 = $artifacts.external_command_dll.sha256
        external_identity_sha256 = $artifacts.external_identity_exe.sha256
        external_native_source_manifest_sha256 = `
            $artifacts.external_native_source_manifest.sha256
        signed_msix_sha256 = $artifacts.signed_msix.sha256
        contract_probe_output_sha256 = $artifacts.contract_probe_output.sha256
        config_sha256 = $artifacts.config.sha256
        config_path_sha256 = Get-Utf16Sha256 $configPath
        invoke_log_sha256 = Get-BytesSha256 $invokeBytes
        uia_transcript_sha256 = $transcriptDigest
    }
    $observations = @(
        [ordered]@{
            source = "selected_item"
            invocation_id = $selectedSequence.invocation_id
            project_sha256 = $selectedSequence.project_sha256
            root_visible_count = $selectedMenu.root_visible_count
            invoke_pattern_available = $selectedMenu.invoke_pattern_available
            expand_collapse_pattern_available = $selectedMenu.expand_collapse_pattern_available
            submenu_item_count = $selectedMenu.submenu_item_count
            gui_visible = $selectedGui.gui_visible
            gui_title = $selectedGui.gui_title
            project_binding_visible = $selectedGui.project_binding_visible
            selection_choice_count = $selectedGui.selection_choice_count
            selection_choice_values_sha256 = $selectedGui.selection_choice_values_sha256
            selected_choice_value_sha256 = $selectedGui.selected_choice_value_sha256
            selection_combo_exact_match_count = $selectedGui.selection_combo_exact_match_count
            library_manager_button_count = $selectedGui.library_manager_button_count
            library_manager_button_text_sha256 = `
                $selectedGui.library_manager_button_text_sha256
            register_button_count = $selectedGui.register_button_count
            register_button_text_sha256 = $selectedGui.register_button_text_sha256
        },
        [ordered]@{
            source = "background_site"
            invocation_id = $backgroundSequence.invocation_id
            project_sha256 = $backgroundSequence.project_sha256
            root_visible_count = $backgroundMenu.root_visible_count
            invoke_pattern_available = $backgroundMenu.invoke_pattern_available
            expand_collapse_pattern_available = $backgroundMenu.expand_collapse_pattern_available
            submenu_item_count = $backgroundMenu.submenu_item_count
            gui_visible = $backgroundGui.gui_visible
            gui_title = $backgroundGui.gui_title
            project_binding_visible = $backgroundGui.project_binding_visible
            selection_choice_count = $backgroundGui.selection_choice_count
            selection_choice_values_sha256 = $backgroundGui.selection_choice_values_sha256
            selected_choice_value_sha256 = $backgroundGui.selected_choice_value_sha256
            selection_combo_exact_match_count = $backgroundGui.selection_combo_exact_match_count
            library_manager_button_count = $backgroundGui.library_manager_button_count
            library_manager_button_text_sha256 = `
                $backgroundGui.library_manager_button_text_sha256
            register_button_count = $backgroundGui.register_button_count
            register_button_text_sha256 = $backgroundGui.register_button_text_sha256
        }
    )
    foreach ($observation in $observations) {
        Assert-Field ($observation.selection_choice_count -eq $expectedChoices.Count) `
            "Unified selector choice count differs from the release config."
        Assert-Field (
            [string]$observation.selection_choice_values_sha256 -ceq
            (Get-CanonicalStringArraySha256 @(
                $expectedChoices | ForEach-Object { [string]$_.label }
            ))
        ) "Unified selector choice digest differs from the configured label/ID mapping."
        Assert-Field ($observation.selection_combo_exact_match_count -eq 1) `
            "Configured labels did not identify exactly one selector combo box."
        Assert-Field $observation.project_binding_visible "Unified GUI did not show the clicked folder."
        Assert-Field ($observation.library_manager_button_count -eq 1) "Library Manager button count is not one."
        Assert-Field ($observation.register_button_count -eq 1) "Register-folder button count is not one."
    }
    $uiReceipts = @(
        New-UiReceiptEvidence `
            "selected_manager_click" $selectedManagerClick "library_manager" "text_sha256"
        New-UiReceiptEvidence `
            "manager_remote" $managerGui "configured_remote" "value_sha256"
        New-UiReceiptEvidence `
            "background_selection" $backgroundGui "selection_choice" "values_sha256"
        New-UiReceiptEvidence `
            "registration_click" $registrationClick "register_selected" "text_sha256"
        New-UiReceiptEvidence `
            "registration_source" $registrationManager "registration_source" "value_sha256"
        New-UiReceiptEvidence `
            "runtime_projectless" $runtimeGui "project" "text_sha256"
    )
    Assert-Field ($uiReceipts.Count -eq 6) `
        "Field evidence must contain all six receipt-bound UI decisions."
    $releaseVersion = ([string]$status.version) -replace '\.0$', ''
    Assert-Field ($releaseVersion -match '^\d+\.\d+\.\d+$') `
        "Installed package version cannot be converted to a release version."
    $fieldStatus = "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_" + `
        $releaseVersion.Replace('.', '_')
    $releaseInputPaths = @(
        ".github/workflows", "integration", "README.md",
        "docs/mvp-redesign.md", "docs/images", "docs/skill-library-management-requirements.md",
        "docs/skill-library-manager-implementation-report-2026-09-02.md",
        "docs/windows-modern-context-menu.md",
        "docs/root-cause-windows-context-root-launch-2026-09-05.md",
        "docs/fix-report-windows-context-root-launch-2026-09-05.md",
        "src", "native", "policy", "tests",
        "setup.py", "pyproject.toml", "skill-magnet.json", ".approved-snapshots"
    )
    $releaseCodeSha = ([string](& git -C $repositoryRoot log -1 --format=%H -- @releaseInputPaths)).Trim().ToLowerInvariant()
    Assert-Field ($LASTEXITCODE -eq 0 -and $releaseCodeSha -match '^[0-9a-f]{40}$') `
        "The field run could not bind evidence to the latest committed release inputs."
    $bundle = [ordered]@{
        schema_version = 5
        release_version = $releaseVersion
        release_code_sha = $releaseCodeSha
        field_status = $fieldStatus
        observed_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        collector_sha256 = Get-NormalizedTextFileSha256 $PSCommandPath
        python_runtime = [ordered]@{
            module_version = [string]$runtime.module_version
            distribution_version = [string]$runtime.distribution_version
            distribution_name = [string]$runtime.distribution_name
            executable_path_sha256 = Get-Utf16Sha256 ([string]$runtime.executable)
            module_path_sha256 = Get-Utf16Sha256 ([string]$runtime.module_path)
            distribution_module_path_sha256 = Get-Utf16Sha256 ([string]$runtime.distribution_module_path)
            payload_sha256 = [string]$runtime.payload_sha256
        }
        package = [ordered]@{
            name = [string]$status.name
            version = [string]$status.version
            architecture = ([string]$status.architecture).ToUpperInvariant()
            publisher = [string]$status.publisher
            package_full_name = [string]$status.package_full_name
            same_name_package_count = [int]$status.same_name_package_count
            expected_identity_match_count = [int]$status.expected_identity_match_count
            unexpected_same_name_package_count = [int]$status.unexpected_same_name_package_count
            usable_installed_state = [bool]$status.usable_installed_state
            menu_contract_matches_config = [bool]$status.menu_contract_matches_config
            command_target_signature_valid = [bool]$status.command_target_signature_valid
        }
        native_source_binding = [ordered]@{
            contract = "skill-magnet-native-source-v1"
            source_tree_sha256 = [string]$expectedNativeSource.source_tree_sha256
            package_manifest_source_tree_sha256 = `
                [string]$packageNativeManifest.source_tree_sha256
            external_manifest_source_tree_sha256 = `
                [string]$externalNativeManifest.source_tree_sha256
            package_dll_export_source_tree_sha256 = `
                [string]$nativeProbeResult.package_export
            external_dll_export_source_tree_sha256 = `
                [string]$nativeProbeResult.external_export
            package_dll_embedded_binding_count = `
                [int]$nativeProbeResult.package_marker_count
            external_dll_embedded_binding_count = `
                [int]$nativeProbeResult.external_marker_count
            package_external_artifacts_equal = $packageExternalArtifactsEqual
            signed_msix_payload_matches_package = $signedMsixPayloadMatchesPackage
            isolated_contract_probe_passed = $true
            isolated_contract_probe_mode = "full-invoke"
            status_native_source_tree_sha256 = `
                [string]$status.native_source_tree_sha256
            status_native_source_manifest_valid = `
                [bool]$status.native_source_manifest_valid
            status_native_artifact_hashes_valid = `
                [bool]$status.native_artifact_hashes_valid
            status_dll_native_source_binding_valid = `
                [bool]$status.dll_native_source_binding_valid
            status_native_build_binding_valid = `
                [bool]$status.native_build_binding_valid
        }
        artifacts = $artifacts
        hashes = $hashes
        uia_transcript = [ordered]@{
            encoding = "utf-8-jsonl"
            line_count = $script:UiaTranscriptLines.Count
            sha256 = $transcriptDigest
            bytes_base64 = [Convert]::ToBase64String($transcriptBytes)
        }
        ui_receipts = $uiReceipts
        selector_contract = [ordered]@{
            choice_map_sha256 = [string]$selectionContract.choice_map_sha256
            ordered_label_sha256 = Get-CanonicalStringArraySha256 @(
                $expectedChoices | ForEach-Object { [string]$_.label }
            )
            choice_count = $expectedChoices.Count
            selected_label_sha256 = Get-Utf8Sha256 ([string]$expectedChoices[0].label)
            exact_selector_combo_count = 1
        }
        explorer_observations = $observations
        library_manager_observation = [ordered]@{
            configured_remote_sha256 = Get-Utf8Sha256 $configuredRemote
            configured_remote_visible = $true
            create_button_count = 1
            create_button_text_sha256 = Get-Utf8Sha256 "新規登録"
            update_button_count = 1
            update_button_text_sha256 = Get-Utf8Sha256 "選択項目を更新"
            delete_button_count = 1
            delete_button_text_sha256 = Get-Utf8Sha256 "選択項目を削除"
            reload_button_count = 1
            reload_button_text_sha256 = Get-Utf8Sha256 "再読込"
            same_folder_repeat_focused_existing_manager = $true
            same_folder_repeat_manager_count = 1
            same_folder_repeat_error_count = 0
            different_folder_busy_text_visible = $true
            different_folder_actionable_recovery_visible = $true
            different_folder_ok_button_count = 1
            no_persistent_mutation = $true
        }
        registration_recovery_observation = [ordered]@{
            selected_path_sha256 = Get-Utf16Sha256 $selectedFolder
            selected_path_visible = $true
            missing_skill_cause_visible = $true
            actionable_recovery_visible = $true
            ok_button_count = 1
            no_persistent_mutation = $true
        }
        runtime_skill_observation = [ordered]@{
            clicked_path_sha256 = Get-Utf16Sha256 $runtimeSkillFolder
            runtime_path_hidden_as_workspace = $true
            projectless_semantics_visible = $true
            skill_content_sha256 = $runtimeFolderAfter
            read_only = $true
        }
        recovery_observations = [ordered]@{
            same_folder_repeat_focused_existing_window = $true
            same_folder_repeat_gui_count = $sameGuiCount
            different_folder_busy_message_visible = $true
            different_folder_actionable_recovery_visible = $true
            closed_window_relaunch_succeeded = $true
        }
        attestation = $null
    }
    $attestationPayload = New-AttestationPayload $bundle
    $bundle.attestation = New-DetachedAttestation $attestationPayload $dllPath
    $bundleJson = $bundle | ConvertTo-Json -Depth 20
    $privateValues = @($configuredRemote) + @(
        $expectedChoices | ForEach-Object { [string]$_.label }
    )
    foreach ($privateValue in $privateValues) {
        Assert-Field (
            -not $privateValue -or
            $bundleJson.IndexOf($privateValue, [StringComparison]::Ordinal) -lt 0
        ) "Field bundle would disclose a raw repository URL or selector label."
    }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $FieldBundle)) | Out-Null
    $FieldBundle = Assert-FieldRegularPathBoundary $FieldBundle $true
    [IO.File]::WriteAllText(
        $FieldBundle,
        $bundleJson,
        (New-Object Text.UTF8Encoding($false))
    )
    Write-Output $fieldStatus
}
finally {
    $ownedCleanupError = $null
    try {
        Register-FieldOwnedProcessesFromInvokeLog
        Close-FieldOwnedUiAndReleaseLease
    }
    catch { $ownedCleanupError = $_ }
    foreach ($window in $windows) {
        try { $window.Quit() } catch { }
    }
    # Field folders contain no user data.  Remove only the generated GUID root.
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
    if ($null -ne $ownedCleanupError) { throw $ownedCleanupError }
}

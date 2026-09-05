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
    $entry = $Archive.GetEntry($Name)
    Assert-Field ($null -ne $entry) "Signed MSIX entry is missing: $Name"
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

function New-AttestationPayload([System.Collections.IDictionary]$Bundle) {
    $selected = @($Bundle.explorer_observations | Where-Object source -eq "selected_item")[0]
    $background = @($Bundle.explorer_observations | Where-Object source -eq "background_site")[0]
    $values = @(
        @("contract", "skill-magnet-windows-explorer-field-v4"),
        @("schema_version", $Bundle.schema_version),
        @("release_version", $Bundle.release_version),
        @("release_code_sha", $Bundle.release_code_sha),
        @("field_status", $Bundle.field_status),
        @("observed_at_utc", $Bundle.observed_at_utc),
        @("collector_sha256", $Bundle.collector_sha256),
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
        @("background_site.invocation_id", $background.invocation_id),
        @("background_site.project_sha256", $background.project_sha256),
        @("selector.choice_map_sha256", $Bundle.selector_contract.choice_map_sha256),
        @("selector.exact_selector_combo_count", $Bundle.selector_contract.exact_selector_combo_count),
        @("library_manager.configured_remote", $Bundle.library_manager_observation.configured_remote),
        @("library_manager.configured_remote_visible", $Bundle.library_manager_observation.configured_remote_visible),
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
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SkillMagnetFieldInput {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(
        uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
    public static void LeftClick(int x, int y) {
        SetCursorPos(x, y); mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
    }
    public static void RightClick(int x, int y) {
        SetCursorPos(x, y); mouse_event(0x0008, 0, 0, 0, UIntPtr.Zero);
        mouse_event(0x0010, 0, 0, 0, UIntPtr.Zero);
    }
}
"@

function Get-VisibleNamedElements([string]$Name) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, $Name
    )
    $matches = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $condition
    )
    @($matches | Where-Object {
        try { -not $_.Current.IsOffscreen } catch { $false }
    })
}

function Get-UiaRuntimeKey($Element) {
    try { (@($Element.GetRuntimeId()) | ForEach-Object { [string]$_ }) -join "." }
    catch { "" }
}

function Wait-VisibleNamedElement([string]$Name, [int]$Seconds = 12) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $matches = @(Get-VisibleNamedElements $Name)
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
    Assert-Field ([SkillMagnetFieldInput]::SetForegroundWindow($handle)) `
        "Could not foreground the Explorer field-test window."
    Start-Sleep -Milliseconds 250
    [System.Windows.Automation.AutomationElement]::FromHandle($handle)
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
        [SkillMagnetFieldInput]::LeftClick($x, $y)
        Start-Sleep -Milliseconds 100
        [SkillMagnetFieldInput]::RightClick($x, $y)
    }
    else {
        $rectangle = $explorer.Current.BoundingRectangle
        $x = [int]($rectangle.Left + ($rectangle.Width * 0.76))
        $y = [int]($rectangle.Top + ($rectangle.Height * 0.72))
        [SkillMagnetFieldInput]::RightClick($x, $y)
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

function Get-SelectionChoiceContract($Gui, [object[]]$ExpectedChoices) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::ComboBox
    )
    $combos = $Gui.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
    $expectedLabels = @($ExpectedChoices | ForEach-Object { [string]$_.label })
    $matching = [Collections.Generic.List[object]]::new()
    $observed = [Collections.Generic.List[object]]::new()
    foreach ($combo in @($combos)) {
        $expand = Get-Pattern $combo ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        if ($null -eq $expand) { continue }
        try {
            $expand.Expand()
            Start-Sleep -Milliseconds 150
            $listCondition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::ListItem
            )
            $visible = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants, $listCondition
            )
            $comboProcessId = [int]$combo.Current.ProcessId
            $items = @($visible | Where-Object {
                try {
                    -not $_.Current.IsOffscreen -and
                    [int]$_.Current.ProcessId -eq $comboProcessId
                } catch { $false }
            } | ForEach-Object { [string]$_.Current.Name })
            $entry = [ordered]@{
                element = Get-UiaElementSnapshot $combo
                labels = $items
            }
            $null = $observed.Add($entry)
            if (Test-ExactStringSequence $items $expectedLabels) {
                $null = $matching.Add($entry)
            }
            $expand.Collapse()
        }
        catch {
            try { $expand.Collapse() } catch { }
        }
    }
    Assert-Field ($matching.Count -eq 1) `
        "Exactly one combo box must expose the configured selector labels; observed $($matching.Count)."
    [ordered]@{
        configured_choices = @($ExpectedChoices)
        labels = @($matching[0].labels)
        exact_match_count = $matching.Count
        combo_box_count = @($combos).Count
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
    [string]$TranscriptSource = ""
) {
    $gui = Wait-VisibleNamedElement "Skill Magnet — 実行確認"
    $projectBound = $false
    $descendants = $gui.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($element in @($descendants)) {
        try {
            if ($element.Current.Name -like "*$ProjectPath*") { $projectBound = $true }
        } catch { }
    }
    $selectionContract = Get-SelectionChoiceContract $gui $ExpectedChoices
    $observation = @{
        element = $gui
        gui_visible = $true
        gui_title = $gui.Current.Name
        project_binding_visible = $projectBound
        selection_choice_count = @($selectionContract.labels).Count
        selection_choice_labels = @($selectionContract.labels)
        selection_combo_exact_match_count = [int]$selectionContract.exact_match_count
        library_manager_button_count = Get-ButtonCount $gui "Library Manager"
        register_button_count = Get-ButtonCount $gui "このフォルダーのスキルを登録"
    }
    if ($TranscriptSource) {
        Add-UiaTranscriptEvent "unified_gui_observed" $TranscriptSource ([ordered]@{
            element = Get-UiaElementSnapshot $gui
            project_sha256 = Get-Utf16Sha256 $ProjectPath
            gui_visible = $observation.gui_visible
            gui_title = $observation.gui_title
            project_binding_visible = $observation.project_binding_visible
            selection_choice_count = $observation.selection_choice_count
            selection_choice_labels = @($observation.selection_choice_labels)
            selection_combo_exact_match_count = $observation.selection_combo_exact_match_count
            library_manager_button_count = $observation.library_manager_button_count
            register_button_count = $observation.register_button_count
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

function Invoke-NamedButton($Window, [string]$Name) {
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
    $buttons = @($Window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $condition
    ))
    Assert-Field ($buttons.Count -eq 1) "Expected one '$Name' button; observed $($buttons.Count)."
    $invoke = Get-Pattern $buttons[0] ([System.Windows.Automation.InvokePattern]::Pattern)
    Assert-Field ($null -ne $invoke) "Button '$Name' has no InvokePattern."
    $invoke.Invoke()
}

function Get-VisibleWindowsByPrefix([string]$Prefix) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window
    )
    $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children, $condition
    )
    @($windows | Where-Object {
        try {
            -not $_.Current.IsOffscreen -and $_.Current.Name.StartsWith(
                $Prefix, [StringComparison]::Ordinal
            )
        }
        catch { $false }
    })
}

function Wait-VisibleWindowByPrefix([string]$Prefix, [int]$Seconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $matches = @(Get-VisibleWindowsByPrefix $Prefix)
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

function Get-UiaControlValues($Window, $ControlType) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ControlType
    )
    $elements = $Window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $condition
    )
    @($elements | ForEach-Object {
        $value = Get-Pattern $_ ([System.Windows.Automation.ValuePattern]::Pattern)
        if ($null -ne $value) { [string]$value.Current.Value }
        else { [string]$_.Current.Name }
    })
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
    $manager = Wait-VisibleWindowByPrefix "Library Manager"
    Assert-Field ([int]$manager.Current.ProcessId -eq $ExpectedProcessId) `
        "Library Manager does not belong to the Explorer-launched process."
    $editValues = @(Get-UiaControlValues $manager ([System.Windows.Automation.ControlType]::Edit))
    $remoteMatches = @($editValues | Where-Object { $_ -ceq $ExpectedRemote }).Count
    Assert-Field ($remoteMatches -eq 1) `
        "Library Manager must show the one configured GitHub URL; observed $remoteMatches matches."
    $crud = [ordered]@{
        create_button_count = Get-ButtonCount $manager "新規登録"
        update_button_count = Get-ButtonCount $manager "選択項目を更新"
        delete_button_count = Get-ButtonCount $manager "選択項目を削除"
        reload_button_count = Get-ButtonCount $manager "再読込"
    }
    foreach ($key in @($crud.Keys)) {
        Assert-Field ([int]$crud[$key] -eq 1) "Library Manager CRUD control '$key' is not unique."
    }
    [ordered]@{
        element = $manager
        element_snapshot = Get-UiaElementSnapshot $manager
        configured_remote = $ExpectedRemote
        configured_remote_visible = $true
        create_button_count = $crud.create_button_count
        update_button_count = $crud.update_button_count
        delete_button_count = $crud.delete_button_count
        reload_button_count = $crud.reload_button_count
        edit_values = $editValues
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
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $lines = @(Read-InvokeLines $Path)
        $newRecords = @($lines | Select-Object -Skip $AfterLineCount | ForEach-Object {
            Parse-InvokeLine $_
        })
        $ids = @($newRecords | Where-Object {
            $_.event -eq "invoke_enter" -and $_.selection_source -eq $Source
        } | ForEach-Object { $_.invocation_id })
        foreach ($id in $ids) {
            $group = @($newRecords | Where-Object { $_.invocation_id -eq $id })
            $events = @($group | ForEach-Object { [string]$_.event })
            if ($events.Count -eq 4 -and
                ($events[0..2] -join ",") -eq (
                    "invoke_enter,selection_succeeded,create_process_succeeded"
                ) -and $TerminalEvents -contains $events[3]) {
                $processId = [int]$group[2].detail
                $terminalDetailValid = if ($events[3] -eq "child_running") {
                    $group[3].detail -eq $group[2].detail
                }
                else {
                    $group[3].detail -eq "0"
                }
                Assert-Field ($processId -gt 0 -and $terminalDetailValid) `
                    "Native success sequence does not identify one successful child process."
                Register-FieldOwnedProcess $processId $id
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
    throw "Native success sequence did not complete for $Source."
}

function Assert-BusyMessageAndClose() {
    $dialog = Wait-VisibleNamedElement "Skill Magnet エラー"
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

$configPath = (Resolve-Path -LiteralPath $Config).Path
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

$runtimeProbe = @'
import hashlib
import importlib.metadata
import json
import pathlib
import sys
import skill_magnet

root = pathlib.Path(skill_magnet.__file__).resolve().parent
module_path = pathlib.Path(skill_magnet.__file__).resolve()
distribution = importlib.metadata.distribution("skill-magnet")
distribution_module_paths = [
    pathlib.Path(distribution.locate_file(item)).resolve()
    for item in (distribution.files or ())
    if item.as_posix() == "skill_magnet/__init__.py"
]
if distribution.metadata.get("Name", "").casefold() != "skill-magnet":
    raise RuntimeError("installed distribution name is not skill-magnet")
if len(distribution_module_paths) != 1 or distribution_module_paths[0] != module_path:
    raise RuntimeError("imported module is not owned by the installed skill-magnet distribution")
digest = hashlib.sha256()
paths = [
    path for path in root.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc"
]
for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
    name = "skill_magnet/" + path.relative_to(root).as_posix()
    content = path.read_bytes()
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
print(json.dumps({
    "module_version": skill_magnet.__version__,
    "distribution_version": distribution.version,
    "distribution_name": distribution.metadata["Name"].casefold(),
    "executable": sys.executable,
    "module_path": str(module_path),
    "distribution_module_path": str(distribution_module_paths[0]),
    "payload_sha256": digest.hexdigest(),
}))
'@
$runtimeJson = $runtimeProbe |
    & ([string]$status.command_target) -I - | Out-String
Assert-Field ($LASTEXITCODE -eq 0) "Installed menu Python runtime probe failed."
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

$invokeLog = Join-Path $env:LOCALAPPDATA "SkillMagnet\ContextMenu\invoke.log"
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
    $selectedGui = Inspect-UnifiedGui $selectedFolder $expectedChoices "selected_item"
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
    Invoke-NamedButton $selectedGui.element "Library Manager"
    $managerGui = Inspect-LibraryManager $configuredRemote $selectedSequence.process_id
    $managerSnapshot = $managerGui.element_snapshot

    # While Manager owns the original context process, repeat the same Explorer
    # invocation.  It must focus the Manager window, not a destroyed selector HWND.
    $before = @(Read-InvokeLines $invokeLog).Count
    Invoke-VisibleSkillMagnetRoot $selectedWindow (Split-Path $selectedFolder -Leaf) | Out-Null
    $managerSameSequence = Wait-NativeSequence `
        $invokeLog "selected_item" $before @("child_running", "child_exited")
    Start-Sleep -Seconds 2
    $visibleManagers = @(Get-VisibleWindowsByPrefix "Library Manager" | Where-Object {
        try { [int]$_.Current.ProcessId -eq $selectedSequence.process_id } catch { $false }
    })
    Assert-Field ($visibleManagers.Count -eq 1) `
        "Same-folder click while Manager is open created or lost a Manager window."
    $managerForeground = [SkillMagnetFieldInput]::GetForegroundWindow()
    $managerHandle = [IntPtr]([int64]$managerGui.element.Current.NativeWindowHandle)
    Assert-Field ($managerForeground -eq $managerHandle) `
        "Same-folder click did not focus the existing Library Manager."
    $managerSameErrors = @(Get-VisibleNamedElements "Skill Magnet エラー")
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
    $managerBusyObservation = Assert-BusyMessageAndClose
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
        configured_remote = $configuredRemote
        configured_remote_visible = $managerGui.configured_remote_visible
        create_button_count = $managerGui.create_button_count
        update_button_count = $managerGui.update_button_count
        delete_button_count = $managerGui.delete_button_count
        reload_button_count = $managerGui.reload_button_count
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
    $backgroundGui = Inspect-UnifiedGui $backgroundFolder $expectedChoices "background_site"
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
    $sameGuis = @(Get-VisibleNamedElements "Skill Magnet — 実行確認")
    $sameGuiCount = $sameGuis.Count
    Assert-Field ($sameGuiCount -eq 1) "Repeated same-folder click created another GUI."
    $focusedWindow = [SkillMagnetFieldInput]::GetForegroundWindow()
    $expectedFocusedWindow = [IntPtr]([int64]$backgroundGui.element.Current.NativeWindowHandle)
    Assert-Field ($focusedWindow -eq $expectedFocusedWindow) `
        "Repeated same-folder click did not focus the existing Skill Magnet GUI."
    $unexpectedErrors = @(Get-VisibleNamedElements "Skill Magnet エラー")
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
    $busyObservation = Assert-BusyMessageAndClose
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
    $relaunched = Inspect-UnifiedGui $backgroundFolder $expectedChoices
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
    $registrationGui = Inspect-UnifiedGui $selectedFolder $expectedChoices
    Assert-Field (
        $registrationSequence.project_sha256 -eq (Get-Utf16Sha256 $selectedFolder)
    ) "Registration invocation did not bind the selected empty folder."
    Assert-Field (
        [int]$registrationGui.element.Current.ProcessId -eq $registrationSequence.process_id
    ) "Registration selector does not belong to the native child process."
    $registrationGuiSnapshot = Get-UiaElementSnapshot $registrationGui.element
    Invoke-NamedButton $registrationGui.element "このフォルダーのスキルを登録"
    $registrationManager = Inspect-LibraryManager `
        $configuredRemote $registrationSequence.process_id
    $registrationEditValues = @($registrationManager.edit_values)
    $selectedPathMatches = @($registrationEditValues | Where-Object {
        $_ -ceq ([IO.Path]::GetFullPath($selectedFolder))
    }).Count
    Assert-Field ($selectedPathMatches -eq 1) `
        "Registration Manager did not carry the Explorer-selected folder exactly once."
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
    $runtimeGui = Inspect-UnifiedGui $runtimeSkillFolder $expectedChoices
    Assert-Field (
        $runtimeSequence.project_sha256 -eq (Get-Utf16Sha256 $runtimeSkillFolder)
    ) "Runtime-skill native digest does not bind the clicked folder."
    Assert-Field (
        [int]$runtimeGui.element.Current.ProcessId -eq $runtimeSequence.process_id
    ) "Runtime-skill GUI does not belong to the native child process."
    $runtimeText = Get-VisibleDescendantText $runtimeGui.element
    $runtimePathHidden = $runtimeText -notlike "*$runtimeSkillFolder*"
    $projectlessVisible = (
        $runtimeText -like "*作業対象フォルダー: 指定なし*" -and
        $runtimeText -like "*デスクトップアプリが新規タスク用領域を自動作成*"
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
        config = New-ArtifactSnapshot `
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
            selection_choice_labels = @($selectedGui.selection_choice_labels)
            selection_combo_exact_match_count = $selectedGui.selection_combo_exact_match_count
            library_manager_button_count = $selectedGui.library_manager_button_count
            register_button_count = $selectedGui.register_button_count
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
            selection_choice_labels = @($backgroundGui.selection_choice_labels)
            selection_combo_exact_match_count = $backgroundGui.selection_combo_exact_match_count
            library_manager_button_count = $backgroundGui.library_manager_button_count
            register_button_count = $backgroundGui.register_button_count
        }
    )
    foreach ($observation in $observations) {
        Assert-Field ($observation.selection_choice_count -eq $expectedChoices.Count) `
            "Unified selector choice count differs from the release config."
        Assert-Field (
            Test-ExactStringSequence `
                @($observation.selection_choice_labels) `
                @($expectedChoices | ForEach-Object { [string]$_.label })
        ) "Unified selector labels differ from the configured label/ID mapping."
        Assert-Field ($observation.selection_combo_exact_match_count -eq 1) `
            "Configured labels did not identify exactly one selector combo box."
        Assert-Field $observation.project_binding_visible "Unified GUI did not show the clicked folder."
        Assert-Field ($observation.library_manager_button_count -eq 1) "Library Manager button count is not one."
        Assert-Field ($observation.register_button_count -eq 1) "Register-folder button count is not one."
    }
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
        schema_version = 4
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
        selector_contract = [ordered]@{
            configured_choices = @($expectedChoices)
            choice_map_sha256 = [string]$selectionContract.choice_map_sha256
            exact_selector_combo_count = 1
        }
        explorer_observations = $observations
        library_manager_observation = [ordered]@{
            configured_remote = $configuredRemote
            configured_remote_visible = $true
            create_button_count = 1
            update_button_count = 1
            delete_button_count = 1
            reload_button_count = 1
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
    [IO.Directory]::CreateDirectory((Split-Path -Parent $FieldBundle)) | Out-Null
    [IO.File]::WriteAllText(
        $FieldBundle,
        ($bundle | ConvertTo-Json -Depth 20),
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

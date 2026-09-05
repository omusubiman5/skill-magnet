param(
    [string]$Configuration = "Release",
    [Parameter(Mandatory = $true)]
    [string]$OutDir,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$BuildNonce,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$MarkerSha256,
    [switch]$SkipContractTest
)

$ErrorActionPreference = "Stop"
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$workspace = Split-Path -Parent $OutDir
$markerPath = Join-Path $workspace ".skill-magnet-native-build.json"
$generatedOutputs = @(
    "ContractTest.exe",
    "ContractTest.obj",
    "SkillMagnetCommand.dll",
    "SkillMagnetCommand.exp",
    "SkillMagnetCommand.lib",
    "SkillMagnetCommand.obj",
    "SkillMagnetIdentity.exe",
    "SkillMagnetIdentity.obj",
    "SkillMagnetMenu.tsv",
    "SkillMagnetNativeSource.h",
    "SkillMagnetNativeSource.json"
)

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public sealed class SkillMagnetDirectoryLease : IDisposable
{
    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime { public uint Low; public uint High; }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation
    {
        public uint Attributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileId128 { public ulong Low; public ulong High; }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileIdInfo
    {
        public ulong VolumeSerialNumber;
        public FileId128 FileId;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string name, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out FileInformation information);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandleEx(
        SafeFileHandle handle, int informationClass,
        out FileIdInfo information, uint size);

    private SafeFileHandle handle;
    public string FileId { get; private set; }
    public string VolumeSerial { get; private set; }

    public SkillMagnetDirectoryLease(string path) : this(path, true, 0x00000003) { }

    public SkillMagnetDirectoryLease(string path, bool directory, uint share)
    {
        // Source files deny both write and delete sharing. Directories permit
        // ordinary reads/writes but deny delete sharing, preventing every path
        // segment from being renamed or rebound during the build.
        uint access = directory ? 0x00000080u : 0x80000000u;
        uint flags = 0x00200000u | (directory ? 0x02000000u : 0u);
        handle = CreateFileW(path, access, share, IntPtr.Zero, 3, flags, IntPtr.Zero);
        if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        FileInformation information;
        if (!GetFileInformationByHandle(handle, out information))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        if ((information.Attributes & 0x00000400) != 0)
            throw new InvalidOperationException("Build workspace cannot be a reparse point.");
        FileIdInfo identity;
        if (!GetFileInformationByHandleEx(
            handle, 18, out identity, (uint)Marshal.SizeOf(typeof(FileIdInfo))))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        byte[] fileId = new byte[16];
        Buffer.BlockCopy(BitConverter.GetBytes(identity.FileId.Low), 0, fileId, 0, 8);
        Buffer.BlockCopy(BitConverter.GetBytes(identity.FileId.High), 0, fileId, 8, 8);
        FileId = BitConverter.ToString(fileId).Replace("-", "").ToLowerInvariant();
        VolumeSerial = identity.VolumeSerialNumber.ToString("x16");
        if (FileId == new string('0', 32) || VolumeSerial == new string('0', 16))
            throw new InvalidOperationException("Build workspace has no volume-bound identity.");
    }

    public void Dispose()
    {
        if (handle != null) handle.Dispose();
    }
}
'@

$sourceInputs = @(
    "AppxManifest.xml",
    "ContractTest.cpp",
    "RecoveryMessages.h",
    "SkillMagnetCommand.cpp",
    "SkillMagnetCommand.def",
    "SkillMagnetIdentity.cpp",
    "SkillMagnetMenu.tsv",
    "build-package.ps1",
    "build.ps1",
    "certificate-state.ps1",
    "contract_test.py",
    "package.ps1"
)

function Get-SkillMagnetSha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    # Windows PowerShell 5.1 runs on .NET Framework, which has neither
    # SHA256.HashData nor Convert.ToHexString.  Keep the build contract usable
    # on the product's supported Windows shell instead of merely parseable.
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($Bytes)
    }
    finally {
        $algorithm.Dispose()
    }
    return [System.BitConverter]::ToString($digest).Replace("-", "").ToLowerInvariant()
}

function Assert-SkillMagnetBuildWorkspace {
    param([switch]$AllowGeneratedOutputs)

    $workspaceItem = Get-Item -LiteralPath $workspace -Force -ErrorAction Stop
    $outputItem = Get-Item -LiteralPath $OutDir -Force -ErrorAction Stop
    if (
        -not $workspaceItem.PSIsContainer -or
        -not $outputItem.PSIsContainer -or
        ($workspaceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        ($outputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        ([System.IO.Path]::GetFileName($OutDir) -cne "out")
    ) {
        throw "OutDir must be the non-reparse out directory of a managed build workspace."
    }
    $workspaceNames = @(
        Get-ChildItem -LiteralPath $workspace -Force | ForEach-Object Name | Sort-Object
    )
    if (($workspaceNames -join "`n") -cne ".skill-magnet-native-build.json`nout") {
        throw "The managed build workspace contains unowned entries."
    }
    $markerItem = Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop
    if (
        $markerItem.PSIsContainer -or
        ($markerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "The managed build workspace marker is invalid."
    }
    $markerBytes = [System.IO.File]::ReadAllBytes($markerPath)
    $markerHash = Get-SkillMagnetSha256Hex -Bytes $markerBytes
    if ($markerHash -cne $MarkerSha256) {
        throw "The managed build workspace marker digest does not match this build."
    }
    $marker = [System.Text.Encoding]::UTF8.GetString($markerBytes) |
        ConvertFrom-Json -ErrorAction Stop
    $markerNames = @($marker.PSObject.Properties.Name | Sort-Object)
    $expectedMarkerNames = @(
        "contract", "nonce", "output_file_id", "root_file_id", "schema_version", "volume_serial"
    ) | Sort-Object
    $canonicalMarker = [ordered]@{
        contract = "skill-magnet-native-build-workspace-v1"
        nonce = $BuildNonce
        output_file_id = [string]$marker.output_file_id
        root_file_id = [string]$marker.root_file_id
        schema_version = 1
        volume_serial = [string]$marker.volume_serial
    }
    $canonicalMarkerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        (($canonicalMarker | ConvertTo-Json -Compress) + "`n")
    )
    if (
        ($markerNames -join "`n") -cne ($expectedMarkerNames -join "`n") -or
        [Convert]::ToBase64String($markerBytes) -cne
            [Convert]::ToBase64String($canonicalMarkerBytes) -or
        $marker.schema_version -ne 1 -or
        $marker.contract -cne "skill-magnet-native-build-workspace-v1" -or
        $marker.nonce -cne $BuildNonce -or
        [string]$marker.volume_serial -cne $workspaceLease.VolumeSerial -or
        [string]$marker.volume_serial -cne $outputLease.VolumeSerial -or
        [string]$marker.root_file_id -cne $workspaceLease.FileId -or
        [string]$marker.output_file_id -cne $outputLease.FileId
    ) {
        throw "The managed build workspace marker does not match this build."
    }
    $entries = @(Get-ChildItem -LiteralPath $OutDir -Force)
    if (-not $AllowGeneratedOutputs -and $entries.Count -ne 0) {
        throw "OutDir must be empty before the native build starts."
    }
    foreach ($entry in $entries) {
        if (
            $entry.PSIsContainer -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            $generatedOutputs -cnotcontains $entry.Name
        ) {
            throw "OutDir contains an unowned entry: $($entry.Name)"
        }
    }
}

$current = Get-Item -LiteralPath $workspace -Force
while ($null -ne $current) {
    if ($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "The managed build workspace has a reparse-point ancestor."
    }
    $current = $current.Parent
}
$workspaceLease = [SkillMagnetDirectoryLease]::new($workspace)
$outputLease = [SkillMagnetDirectoryLease]::new($OutDir)
$sourceDirectoryLeases = [System.Collections.Generic.List[SkillMagnetDirectoryLease]]::new()
$sourceFileLeases = [System.Collections.Generic.List[SkillMagnetDirectoryLease]]::new()
try {
$sourceCurrent = Get-Item -LiteralPath $PSScriptRoot -Force -ErrorAction Stop
while ($null -ne $sourceCurrent) {
    if (
        -not ($sourceCurrent -is [System.IO.DirectoryInfo]) -or
        ($sourceCurrent.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "The native source has a reparse-point ancestor."
    }
    $sourceDirectoryLeases.Add(
        [SkillMagnetDirectoryLease]::new($sourceCurrent.FullName, $true, 0x00000003)
    )
    $sourceCurrent = $sourceCurrent.Parent
}
foreach ($relativePath in $sourceInputs) {
    $sourcePath = Join-Path $PSScriptRoot $relativePath
    $sourceItem = Get-Item -LiteralPath $sourcePath -Force -ErrorAction Stop
    if (
        -not ($sourceItem -is [System.IO.FileInfo]) -or
        ($sourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Native provenance input is unsafe: $relativePath"
    }
    $sourceFileLeases.Add(
        [SkillMagnetDirectoryLease]::new($sourcePath, $false, 0x00000001)
    )
}
Assert-SkillMagnetBuildWorkspace
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) { throw "Visual C++ x64 BuildTools are required." }
$devShell = Join-Path $install "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Import-Module $devShell
Enter-VsDevShell -VsInstallPath $install -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64" | Out-Null

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$combined = [System.IO.MemoryStream]::new()
$inputRecords = @()
try {
    foreach ($relativePath in $sourceInputs) {
        $sourcePath = Join-Path $PSScriptRoot $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Native provenance input is missing: $relativePath"
        }
        $rawText = [System.IO.File]::ReadAllText($sourcePath, $utf8NoBom)
        $normalizedText = $rawText.Replace("`r`n", "`n")
        $normalizedBytes = $utf8NoBom.GetBytes($normalizedText)
        $nameBytes = $utf8NoBom.GetBytes($relativePath)
        $combined.Write($nameBytes, 0, $nameBytes.Length)
        $combined.WriteByte(0)
        $combined.Write($normalizedBytes, 0, $normalizedBytes.Length)
        $combined.WriteByte(0)
        $fileHash = Get-SkillMagnetSha256Hex -Bytes $normalizedBytes
        $inputRecords += [ordered]@{
            path = $relativePath
            normalized_size = $normalizedBytes.Length
            sha256 = $fileHash
        }
    }
    $sourceTreeHash = Get-SkillMagnetSha256Hex -Bytes $combined.ToArray()
}
finally {
    $combined.Dispose()
}
$sourceTreeSha256 = $sourceTreeHash
$sourceManifest = [ordered]@{
    schema_version = 1
    contract = "skill-magnet-native-source-v1"
    source_tree_sha256 = $sourceTreeSha256
    inputs = $inputRecords
}
$sourceManifestPath = Join-Path $OutDir "SkillMagnetNativeSource.json"
[System.IO.File]::WriteAllText(
    $sourceManifestPath,
    (($sourceManifest | ConvertTo-Json -Depth 5 -Compress) + "`n"),
    $utf8NoBom
)
$sourceDigestHeader = Join-Path $OutDir "SkillMagnetNativeSource.h"
[System.IO.File]::WriteAllText(
    $sourceDigestHeader,
    ("#pragma once`n#define SKILL_MAGNET_NATIVE_SOURCE_SHA256 L`"" +
        $sourceTreeSha256 + "`"`n"),
    $utf8NoBom
)
$common = @(
    "/nologo", "/utf-8", "/std:c++20", "/EHsc", "/W4", "/WX",
    "/DUNICODE", "/D_UNICODE", ("/FI" + $sourceDigestHeader)
)
& cl @common "/LD" "/Fo:$OutDir\SkillMagnetCommand.obj" "$PSScriptRoot\SkillMagnetCommand.cpp" "/link" "/WX" "/OUT:$OutDir\SkillMagnetCommand.dll" "/IMPLIB:$OutDir\SkillMagnetCommand.lib" "/DEF:$PSScriptRoot\SkillMagnetCommand.def" "bcrypt.lib" "ole32.lib" "shell32.lib" "user32.lib"
if ($LASTEXITCODE -ne 0) { throw "SkillMagnetCommand.dll build failed ($LASTEXITCODE)." }
Copy-Item -Force "$PSScriptRoot\SkillMagnetMenu.tsv" "$OutDir\SkillMagnetMenu.tsv"
& cl @common "/Fo:$OutDir\ContractTest.obj" "$PSScriptRoot\ContractTest.cpp" "/link" "/WX" "/OUT:$OutDir\ContractTest.exe" "bcrypt.lib" "ole32.lib" "shell32.lib"
if ($LASTEXITCODE -ne 0) { throw "ContractTest.exe build failed ($LASTEXITCODE)." }
& cl @common "/Fo:$OutDir\SkillMagnetIdentity.obj" "$PSScriptRoot\SkillMagnetIdentity.cpp" "/link" "/WX" "/SUBSYSTEM:WINDOWS" "/OUT:$OutDir\SkillMagnetIdentity.exe"
if ($LASTEXITCODE -ne 0) { throw "SkillMagnetIdentity.exe build failed ($LASTEXITCODE)." }
$sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Directory |
    Where-Object Name -Match '^10\.' | Sort-Object Name -Descending | Select-Object -First 1
$signTool = if ($sdk) { Join-Path $sdk.FullName "x64\signtool.exe" } else { $null }
$certificate = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Subject -eq "CN=Skill Magnet Local" -and $_.HasPrivateKey -and
        (Test-Path -LiteralPath ("Cert:\LocalMachine\TrustedPeople\" + $_.Thumbprint))
    } |
    Select-Object -First 1
if ($certificate -and $signTool -and (Test-Path -LiteralPath $signTool)) {
    foreach ($binary in @("$OutDir\SkillMagnetCommand.dll", "$OutDir\SkillMagnetIdentity.exe")) {
        & $signTool sign /fd SHA256 /s My /sha1 $certificate.Thumbprint $binary
        if ($LASTEXITCODE -ne 0) { throw "Native binary signing failed ($LASTEXITCODE): $binary" }
    }
}
$artifactRecords = @()
foreach ($artifactName in @("SkillMagnetCommand.dll", "SkillMagnetIdentity.exe")) {
    $artifactPath = Join-Path $OutDir $artifactName
    $artifactBytes = [System.IO.File]::ReadAllBytes($artifactPath)
    $artifactHash = Get-SkillMagnetSha256Hex -Bytes $artifactBytes
    $artifactRecords += [ordered]@{
        path = $artifactName
        size = $artifactBytes.Length
        sha256 = $artifactHash
    }
}
$sourceManifest["artifacts"] = $artifactRecords
[System.IO.File]::WriteAllText(
    $sourceManifestPath,
    (($sourceManifest | ConvertTo-Json -Depth 5 -Compress) + "`n"),
    $utf8NoBom
)
if (-not $SkipContractTest) {
    try {
        & "$OutDir\ContractTest.exe" "$OutDir\SkillMagnetCommand.dll"
        if ($LASTEXITCODE -ne 0) { throw "Native COM contract test failed ($LASTEXITCODE)." }
    }
    catch [System.Management.Automation.ApplicationFailedException] {
        & py -3.12 "$PSScriptRoot\contract_test.py" "$OutDir\SkillMagnetCommand.dll" `
            --invoke "$PSScriptRoot"
        if ($LASTEXITCODE -ne 0) { throw "Native COM Python-host contract test failed ($LASTEXITCODE)." }
    }
}
for ($index = 0; $index -lt $sourceInputs.Count; $index++) {
    $relativePath = $sourceInputs[$index]
    $rawText = [System.IO.File]::ReadAllText(
        (Join-Path $PSScriptRoot $relativePath), $utf8NoBom
    )
    $normalizedBytes = $utf8NoBom.GetBytes($rawText.Replace("`r`n", "`n"))
    $observedHash = Get-SkillMagnetSha256Hex -Bytes $normalizedBytes
    if (
        $inputRecords[$index].path -cne $relativePath -or
        $inputRecords[$index].normalized_size -ne $normalizedBytes.Length -or
        $inputRecords[$index].sha256 -cne $observedHash
    ) {
        throw "Native provenance input changed during the build: $relativePath"
    }
}
Assert-SkillMagnetBuildWorkspace -AllowGeneratedOutputs
}
finally {
    for ($index = $sourceFileLeases.Count - 1; $index -ge 0; $index--) {
        $sourceFileLeases[$index].Dispose()
    }
    for ($index = $sourceDirectoryLeases.Count - 1; $index -ge 0; $index--) {
        $sourceDirectoryLeases[$index].Dispose()
    }
    if ($null -ne $outputLease) { $outputLease.Dispose() }
    if ($null -ne $workspaceLease) { $workspaceLease.Dispose() }
}

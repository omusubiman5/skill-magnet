param(
    [string]$Configuration = "Release",
    [string]$OutDir = "$PSScriptRoot\out",
    [switch]$SkipContractTest
)

$ErrorActionPreference = "Stop"
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) { throw "Visual C++ x64 BuildTools are required." }
$devShell = Join-Path $install "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Import-Module $devShell
Enter-VsDevShell -VsInstallPath $install -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64" | Out-Null

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Remove-Item -LiteralPath (Join-Path $OutDir "SkillMagnetLauncher.exe") -Force -ErrorAction SilentlyContinue
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
        $fileHash = [System.Security.Cryptography.SHA256]::HashData($normalizedBytes)
        $inputRecords += [ordered]@{
            path = $relativePath
            normalized_size = $normalizedBytes.Length
            sha256 = [Convert]::ToHexString($fileHash).ToLowerInvariant()
        }
    }
    $sourceTreeHash = [System.Security.Cryptography.SHA256]::HashData($combined.ToArray())
}
finally {
    $combined.Dispose()
}
$sourceTreeSha256 = [Convert]::ToHexString($sourceTreeHash).ToLowerInvariant()
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
    $artifactHash = [System.Security.Cryptography.SHA256]::HashData($artifactBytes)
    $artifactRecords += [ordered]@{
        path = $artifactName
        size = $artifactBytes.Length
        sha256 = [Convert]::ToHexString($artifactHash).ToLowerInvariant()
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

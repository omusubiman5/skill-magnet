param(
    [string]$Configuration = "Release",
    [string]$OutDir = "$PSScriptRoot\out",
    [switch]$SkipContractTest
)

$ErrorActionPreference = "Stop"
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) { throw "Visual C++ x64 BuildTools are required." }
$devShell = Join-Path $install "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Import-Module $devShell
Enter-VsDevShell -VsInstallPath $install -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64" | Out-Null

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$common = @("/nologo", "/std:c++20", "/EHsc", "/W4", "/WX", "/DUNICODE", "/D_UNICODE")
& cl @common "/LD" "$PSScriptRoot\SkillMagnetCommand.cpp" "/link" "/OUT:$OutDir\SkillMagnetCommand.dll" "/EXPORT:DllGetClassObject" "/EXPORT:DllCanUnloadNow" "ole32.lib" "user32.lib"
if ($LASTEXITCODE -ne 0) { throw "SkillMagnetCommand.dll build failed ($LASTEXITCODE)." }
Copy-Item -Force "$PSScriptRoot\SkillMagnetMenu.tsv" "$OutDir\SkillMagnetMenu.tsv"
& cl @common "$PSScriptRoot\ContractTest.cpp" "/link" "/OUT:$OutDir\ContractTest.exe" "ole32.lib"
if ($LASTEXITCODE -ne 0) { throw "ContractTest.exe build failed ($LASTEXITCODE)." }
& cl @common "$PSScriptRoot\SkillMagnetLauncher.cpp" "/link" "/SUBSYSTEM:WINDOWS" "/OUT:$OutDir\SkillMagnetLauncher.exe"
if ($LASTEXITCODE -ne 0) { throw "SkillMagnetLauncher.exe build failed ($LASTEXITCODE)." }
if (-not $SkipContractTest) {
    & "$OutDir\ContractTest.exe" "$OutDir\SkillMagnetCommand.dll"
    if ($LASTEXITCODE -ne 0) { throw "Native COM contract test failed ($LASTEXITCODE)." }
}

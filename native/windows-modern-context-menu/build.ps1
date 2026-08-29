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
Remove-Item -LiteralPath (Join-Path $OutDir "SkillMagnetLauncher.exe") -Force -ErrorAction SilentlyContinue
$common = @("/nologo", "/std:c++20", "/EHsc", "/W4", "/WX", "/DUNICODE", "/D_UNICODE")
& cl @common "/LD" "/Fo:$OutDir\SkillMagnetCommand.obj" "$PSScriptRoot\SkillMagnetCommand.cpp" "/link" "/OUT:$OutDir\SkillMagnetCommand.dll" "/IMPLIB:$OutDir\SkillMagnetCommand.lib" "/EXPORT:DllGetClassObject" "/EXPORT:DllCanUnloadNow" "bcrypt.lib" "ole32.lib" "user32.lib"
if ($LASTEXITCODE -ne 0) { throw "SkillMagnetCommand.dll build failed ($LASTEXITCODE)." }
Copy-Item -Force "$PSScriptRoot\SkillMagnetMenu.tsv" "$OutDir\SkillMagnetMenu.tsv"
& cl @common "/Fo:$OutDir\ContractTest.obj" "$PSScriptRoot\ContractTest.cpp" "/link" "/OUT:$OutDir\ContractTest.exe" "ole32.lib"
if ($LASTEXITCODE -ne 0) { throw "ContractTest.exe build failed ($LASTEXITCODE)." }
& cl @common "/Fo:$OutDir\SkillMagnetIdentity.obj" "$PSScriptRoot\SkillMagnetIdentity.cpp" "/link" "/SUBSYSTEM:WINDOWS" "/OUT:$OutDir\SkillMagnetIdentity.exe"
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
if (-not $SkipContractTest) {
    try {
        & "$OutDir\ContractTest.exe" "$OutDir\SkillMagnetCommand.dll"
        if ($LASTEXITCODE -ne 0) { throw "Native COM contract test failed ($LASTEXITCODE)." }
    }
    catch [System.Management.Automation.ApplicationFailedException] {
        & py -3.12 "$PSScriptRoot\contract_test.py" "$OutDir\SkillMagnetCommand.dll"
        if ($LASTEXITCODE -ne 0) { throw "Native COM Python-host contract test failed ($LASTEXITCODE)." }
    }
}

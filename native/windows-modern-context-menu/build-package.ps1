param(
    [Parameter(Mandatory=$true)]
    [string]$ExternalLocation
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "certificate-state.ps1")
Import-Module Microsoft.PowerShell.Security
Import-Module PKI
$subject = "CN=Skill Magnet Local"
$statePath = Join-Path $ExternalLocation "certificate-state.json"
$previousState = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $candidateState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($candidateState.thumbprint -match '^[0-9A-Fa-f]{40}$') {
            $previousState = $candidateState
        }
    }
    catch {
        throw "Existing certificate ownership state is invalid; refusing to overwrite it."
    }
}
$sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Directory |
    Where-Object Name -Match '^10\.' | Sort-Object Name -Descending | Select-Object -First 1
if (-not $sdk) { throw "Windows SDK tools are required." }
$makeAppx = Join-Path $sdk.FullName "x64\makeappx.exe"
$signTool = Join-Path $sdk.FullName "x64\signtool.exe"
if (-not (Test-Path -LiteralPath $makeAppx) -or -not (Test-Path -LiteralPath $signTool)) {
    throw "makeappx.exe and signtool.exe are required."
}

$certificates = @(
    Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey }
)
# Reuse a certificate already trusted by the machine whenever possible. A new
# thumbprint would force an unexpected UAC prompt during an otherwise routine
# per-user menu update.
$certificate = $certificates |
    Where-Object {
        Test-Path -LiteralPath ("Cert:\LocalMachine\TrustedPeople\" + $_.Thumbprint)
    } |
    Select-Object -First 1
if (-not $certificate) {
    $certificate = $certificates | Select-Object -First 1
}
$createdMy = $false
if (-not $certificate) {
    $certificate = New-SelfSignedCertificate -Type Custom -Subject $subject `
        -FriendlyName "Skill Magnet local package signing" `
        -KeyUsage DigitalSignature -KeyExportPolicy Exportable `
        -CertStoreLocation Cert:\CurrentUser\My `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    $createdMy = $true
}
$trusted = Test-Path -LiteralPath ("Cert:\CurrentUser\TrustedPeople\" + $certificate.Thumbprint)
$createdTrust = -not $trusted

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("skill-magnet-package-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $cer = Join-Path $ExternalLocation "SkillMagnet.cer"
    Export-Certificate -Cert $certificate -FilePath $cer | Out-Null
    if ($createdTrust) {
        Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\TrustedPeople | Out-Null
    }
    $layout = Join-Path $temporary "layout"
    New-Item -ItemType Directory -Path $layout | Out-Null
    Copy-Item -LiteralPath (Join-Path $ExternalLocation "AppxManifest.xml") -Destination (Join-Path $layout "AppxManifest.xml")
    $package = Join-Path $ExternalLocation "SkillMagnet.ContextMenu.msix"
    & $makeAppx pack /d $layout /p $package /nv /o
    if ($LASTEXITCODE -ne 0) { throw "makeappx failed ($LASTEXITCODE)." }
    foreach ($binaryName in @("SkillMagnetCommand.dll", "SkillMagnetLauncher.exe")) {
        $binary = Join-Path $ExternalLocation $binaryName
        if (-not (Test-Path -LiteralPath $binary)) {
            throw "Required native binary is missing: $binaryName"
        }
        & $signTool sign /fd SHA256 /s My /sha1 $certificate.Thumbprint $binary
        if ($LASTEXITCODE -ne 0) { throw "signtool failed for $binaryName ($LASTEXITCODE)." }
    }
    & $signTool sign /fd SHA256 /s My /sha1 $certificate.Thumbprint $package
    if ($LASTEXITCODE -ne 0) { throw "signtool failed ($LASTEXITCODE)." }
    Merge-SkillMagnetCertificateState `
        -PreviousState $previousState `
        -Thumbprint $certificate.Thumbprint `
        -CreatedMy $createdMy `
        -CreatedTrustedPeople $createdTrust |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}
catch {
    Remove-Item -LiteralPath (Join-Path $ExternalLocation "SkillMagnet.cer") -ErrorAction SilentlyContinue
    if ($createdTrust) { Remove-Item -LiteralPath ("Cert:\CurrentUser\TrustedPeople\" + $certificate.Thumbprint) -ErrorAction SilentlyContinue }
    if ($createdMy) { & certutil.exe -user -delstore My $certificate.Thumbprint | Out-Null }
    throw
}
finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}

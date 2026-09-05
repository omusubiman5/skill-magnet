param(
    [Parameter(Mandatory=$true)]
    [string]$ExternalLocation
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "certificate-state.ps1")
Import-Module Microsoft.PowerShell.Security
Import-Module PKI
$subject = $script:SkillMagnetCertificateSubject
$statePath = Join-Path $ExternalLocation "certificate-state.json"
$previousState = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $candidateState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $candidateState = Assert-SkillMagnetCertificateState -State $candidateState
        $candidateMyPath = (
            "Cert:\CurrentUser\My\" +
            ([string]$candidateState.thumbprint).ToUpperInvariant()
        )
        if (-not (Test-Path -LiteralPath $candidateMyPath)) {
            throw "matching CurrentUser\\My certificate is missing"
        }
        $candidateCertificate = Get-Item -LiteralPath $candidateMyPath
        Assert-SkillMagnetCertificateDeletionOwnership `
            -State $candidateState `
            -CurrentUserMyCertificate $candidateCertificate | Out-Null
        $previousState = $candidateState
    }
    catch {
        throw (
            "Existing certificate ownership state is invalid or unverifiable; " +
            "refusing to overwrite it. " + $_.Exception.Message
        )
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
        Where-Object { Test-SkillMagnetProductCertificate -Certificate $_ }
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
        -FriendlyName $script:SkillMagnetCertificateFriendlyName `
        -KeyUsage DigitalSignature -KeyExportPolicy Exportable `
        -CertStoreLocation Cert:\CurrentUser\My `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    $createdMy = $true
}
if (-not (Test-SkillMagnetProductCertificate `
    -Certificate $certificate `
    -ExpectedThumbprint ([string]$certificate.Thumbprint))) {
    throw "The selected signing certificate is not a verified Skill Magnet product certificate."
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
    foreach ($binaryName in @("SkillMagnetCommand.dll", "SkillMagnetIdentity.exe")) {
        $binary = Join-Path $ExternalLocation $binaryName
        if (-not (Test-Path -LiteralPath $binary)) {
            throw "Required native binary is missing: $binaryName"
        }
        & $signTool sign /fd SHA256 /s My /sha1 $certificate.Thumbprint $binary
        if ($LASTEXITCODE -ne 0) { throw "signtool failed for $binaryName ($LASTEXITCODE)." }
    }
    $nativeSourcePath = Join-Path $ExternalLocation "SkillMagnetNativeSource.json"
    if (-not (Test-Path -LiteralPath $nativeSourcePath -PathType Leaf)) {
        throw "Required native source manifest is missing: SkillMagnetNativeSource.json"
    }
    try {
        $nativeSource = Get-Content -LiteralPath $nativeSourcePath -Raw | ConvertFrom-Json
    }
    catch {
        throw "Native source manifest is not valid JSON."
    }
    if (
        $nativeSource.schema_version -ne 1 -or
        $nativeSource.contract -ne "skill-magnet-native-source-v1" -or
        ([string]$nativeSource.source_tree_sha256) -notmatch '^[0-9a-f]{64}$' -or
        @($nativeSource.inputs).Count -eq 0
    ) {
        throw "Native source manifest contract is invalid."
    }
    $artifactRecords = @()
    foreach ($artifactName in @("SkillMagnetCommand.dll", "SkillMagnetIdentity.exe")) {
        $artifactPath = Join-Path $ExternalLocation $artifactName
        $artifactBytes = [System.IO.File]::ReadAllBytes($artifactPath)
        $artifactHash = [System.Security.Cryptography.SHA256]::HashData($artifactBytes)
        $artifactRecords += [ordered]@{
            path = $artifactName
            size = $artifactBytes.Length
            sha256 = [Convert]::ToHexString($artifactHash).ToLowerInvariant()
        }
    }
    $nativeSource | Add-Member -NotePropertyName artifacts `
        -NotePropertyValue $artifactRecords -Force
    [System.IO.File]::WriteAllText(
        $nativeSourcePath,
        (($nativeSource | ConvertTo-Json -Depth 5 -Compress) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    $layout = Join-Path $temporary "layout"
    New-Item -ItemType Directory -Path $layout | Out-Null
    foreach ($fileName in @(
        "AppxManifest.xml",
        "SkillMagnetCommand.dll",
        "SkillMagnetIdentity.exe",
        "SkillMagnetNativeSource.json",
        "SkillMagnetMenu.tsv"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $ExternalLocation $fileName) -PathType Leaf)) {
            throw "Required package input is missing: $fileName"
        }
        Copy-Item -LiteralPath (Join-Path $ExternalLocation $fileName) `
            -Destination (Join-Path $layout $fileName)
    }
    Copy-Item -LiteralPath (Join-Path $ExternalLocation "Assets") `
        -Destination (Join-Path $layout "Assets") -Recurse
    $package = Join-Path $ExternalLocation "SkillMagnet.ContextMenu.msix"
    & $makeAppx pack /d $layout /p $package /nv /o
    if ($LASTEXITCODE -ne 0) { throw "makeappx failed ($LASTEXITCODE)." }
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

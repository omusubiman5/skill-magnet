$ErrorActionPreference = "Stop"

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$originalLocalAppData = $env:LOCALAPPDATA
$originalTrustMode = $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST
$testBase = Join-Path $env:RUNNER_TEMP ("skill-magnet-release-" + [guid]::NewGuid())
$installRoot = Join-Path $testBase "SkillMagnet\ContextMenu"
$thumbprint = $null
$legacyThumbprints = @()

try {
    New-Item -ItemType Directory -Path $testBase | Out-Null
    $env:LOCALAPPDATA = $testBase
    $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST = "1"

    foreach ($index in 1..2) {
        $legacy = New-SelfSignedCertificate -Type Custom `
            -Subject "CN=Skill Magnet Local" `
            -FriendlyName "Skill Magnet local package signing" `
            -KeyUsage DigitalSignature -KeyExportPolicy Exportable `
            -CertStoreLocation Cert:\CurrentUser\My `
            -TextExtension @(
                "2.5.29.37={text}1.3.6.1.5.5.7.3.3",
                "2.5.29.19={text}"
            )
        $legacyThumbprints += $legacy.Thumbprint
        $legacyCer = Join-Path $testBase "legacy-$index.cer"
        Export-Certificate -Cert $legacy -FilePath $legacyCer | Out-Null
        Import-Certificate -FilePath $legacyCer `
            -CertStoreLocation Cert:\CurrentUser\TrustedPeople | Out-Null
        Import-Certificate -FilePath $legacyCer `
            -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
        Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $legacy.Thumbprint) -Force
    }

    $installOutput = python -m skill_magnet install-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu installation failed."
    $installed = $installOutput | ConvertFrom-Json
    Assert-True ([bool]$installed.modern.usable_installed_state) `
        "The real MSIX installation did not become usable."
    foreach ($legacyThumbprint in $legacyThumbprints) {
        Assert-True (-not (Test-Path -LiteralPath (
            "Cert:\CurrentUser\TrustedPeople\" + $legacyThumbprint
        ))) "Legacy user trust certificate remains after upgrade."
        Assert-True (-not (Test-Path -LiteralPath (
            "Cert:\LocalMachine\TrustedPeople\" + $legacyThumbprint
        ))) "Legacy machine trust certificate remains after upgrade."
    }

    $statePath = Join-Path $installRoot "certificate-state.json"
    Assert-True (Test-Path -LiteralPath $statePath -PathType Leaf) `
        "Certificate ownership state is missing."
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $thumbprint = [string]$state.thumbprint
    Assert-True ($thumbprint -match '^[0-9A-Fa-f]{40}$') `
        "Certificate ownership thumbprint is invalid."

    $statusOutput = python -m skill_magnet context-menu-status `
        --platform windows | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu status failed."
    $status = $statusOutput | ConvertFrom-Json
    Assert-True ([bool]$status.usable_installed_state) `
        "Installed release status is not usable."
    Assert-True ([int]$status.menu_leaf_count -eq 1) `
        "Installed release does not expose exactly one package leaf."

    $rollbackOutput = python -m skill_magnet uninstall-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu rollback/uninstall failed."
    $rollback = $rollbackOutput | ConvertFrom-Json
    Assert-True ([bool]$rollback.rollback_point_removed) `
        "Rollback point was not removed."

    Assert-True (-not (Get-AppxPackage -Name "SkillMagnet.ContextMenu")) `
        "MSIX package remains installed."
    foreach ($storePath in @(
        "Cert:\CurrentUser\My\$thumbprint",
        "Cert:\CurrentUser\TrustedPeople\$thumbprint",
        "Cert:\LocalMachine\TrustedPeople\$thumbprint"
    )) {
        Assert-True (-not (Test-Path -LiteralPath $storePath)) `
            "Owned certificate remains in $storePath."
    }
    Assert-True (-not (Test-Path -LiteralPath $installRoot)) `
        "External install root remains after rollback."
    foreach ($registryRoot in @(
        "HKCU:\Software\Classes\Directory\shell\SkillMagnetClassic",
        "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
        "HKCU:\Software\Classes\Directory\shell\SkillMagnet",
        "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnet"
    )) {
        Assert-True (-not (Test-Path -LiteralPath $registryRoot)) `
            "Owned registry root remains at $registryRoot."
    }

    Write-Host "windows-release-lifecycle-tests: OK"
}
finally {
    Get-AppxPackage -Name "SkillMagnet.ContextMenu" -ErrorAction SilentlyContinue |
        Remove-AppxPackage -ErrorAction SilentlyContinue
    if ($thumbprint) {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" `
            -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "Cert:\CurrentUser\TrustedPeople\$thumbprint" `
            -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "Cert:\LocalMachine\TrustedPeople\$thumbprint" `
            -Force -ErrorAction SilentlyContinue
    }
    foreach ($legacyThumbprint in $legacyThumbprints) {
        Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $legacyThumbprint) `
            -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ("Cert:\CurrentUser\TrustedPeople\" + $legacyThumbprint) `
            -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ("Cert:\LocalMachine\TrustedPeople\" + $legacyThumbprint) `
            -Force -ErrorAction SilentlyContinue
    }
    foreach ($registryRoot in @(
        "HKCU:\Software\Classes\Directory\shell\SkillMagnetClassic",
        "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
        "HKCU:\Software\Classes\Directory\shell\SkillMagnet",
        "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnet"
    )) {
        Remove-Item -LiteralPath $registryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $testBase) {
        Remove-Item -LiteralPath $testBase -Recurse -Force
    }
    $env:LOCALAPPDATA = $originalLocalAppData
    $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST = $originalTrustMode
}

$ErrorActionPreference = "Stop"
$lifecycleTranscript = $env:SKILL_MAGNET_LIFECYCLE_TRANSCRIPT
if ($lifecycleTranscript) {
    Start-Transcript -LiteralPath $lifecycleTranscript -Force | Out-Null
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$originalLocalAppData = $env:LOCALAPPDATA
$originalTrustMode = $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST
$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
$testParent = if ($env:RUNNER_TEMP) {
    $env:RUNNER_TEMP
}
else {
    [IO.Path]::GetTempPath()
}
$testBase = Join-Path $testParent ("skill-magnet-release-" + [guid]::NewGuid())
$installRoot = Join-Path $testBase "SkillMagnet\ContextMenu"
$thumbprint = $null
$legacyThumbprints = @()

# The lifecycle test uses the production Appx identity.  Never let a local or
# self-hosted run silently uninstall an existing user installation or erase its
# rollback state.  An operator may only override this after explicitly taking
# responsibility for preserving that installation.
$preexistingState = @()
if (Get-AppxPackage -Name "SkillMagnet.ContextMenu" -ErrorAction SilentlyContinue) {
    $preexistingState += "Appx package"
}
$realProductParent = Join-Path $originalLocalAppData "SkillMagnet"
foreach ($name in @("ContextMenu", "ContextMenu.rollback", "ContextMenu.rollback.update")) {
    if (Test-Path -LiteralPath (Join-Path $realProductParent $name)) {
        $preexistingState += $name
    }
}
foreach ($registryRoot in @(
    "HKCU:\Software\Classes\Directory\shell\SkillMagnetClassic",
    "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
    "HKCU:\Software\Classes\Directory\shell\SkillMagnet",
    "HKCU:\Software\Classes\Directory\Background\shell\SkillMagnet"
)) {
    if (Test-Path -LiteralPath $registryRoot) {
        $preexistingState += $registryRoot
    }
}
if ($preexistingState.Count -gt 0 -and
    $env:SKILL_MAGNET_ALLOW_DESTRUCTIVE_LIFECYCLE -ne "1") {
    throw ("Refusing to run the destructive release lifecycle over an existing " +
        "Skill Magnet installation: " + ($preexistingState -join ", ") +
        ". Use a clean Windows account/runner. The explicit override is reserved " +
        "for operators who have independently backed up the installation.")
}

try {
    New-Item -ItemType Directory -Path $testBase | Out-Null
    $env:LOCALAPPDATA = $testBase
    $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST = if ($isElevated) { "1" } else { $null }

    if ($isElevated) {
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
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
        [ordered]@{
            thumbprint = $legacyThumbprints[-1]
            created_my = $false
            created_trusted_people = $false
            created_machine_trusted_people = $false
            owned_certificate_thumbprints = @($legacyThumbprints)
        } | ConvertTo-Json | Set-Content `
            -LiteralPath (Join-Path $installRoot "certificate-state.json") `
            -Encoding UTF8
    }

    $defaultConfig = python -c "from skill_magnet.cli import _default_config_path; print(_default_config_path())"
    $priorConfig = Join-Path $testBase "prior-skill-magnet.json"
    $prior = Get-Content -LiteralPath $defaultConfig -Raw | ConvertFrom-Json
    $prior.packs[0].purpose = "Prior installed release used only by the rollback lifecycle."
    $prior | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $priorConfig -Encoding UTF8

    $installOutput = python -m skill_magnet --config $priorConfig install-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu installation failed."
    $installed = $installOutput | ConvertFrom-Json
    Assert-True ([bool]$installed.modern.usable_installed_state) `
        "The real MSIX installation did not become usable."
    $priorMenuHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath (Join-Path $installRoot "SkillMagnetMenu.tsv")).Hash
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

    $updateOutput = python -m skill_magnet install-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu update failed."
    $updated = $updateOutput | ConvertFrom-Json
    Assert-True ([bool]$updated.modern.usable_installed_state) `
        "The updated real MSIX installation did not become usable."
    $updatedMenuHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath (Join-Path $installRoot "SkillMagnetMenu.tsv")).Hash
    Assert-True ($updatedMenuHash -ne $priorMenuHash) `
        "The real update did not change its menu contract."

    $statusOutput = python -m skill_magnet context-menu-status `
        --platform windows | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu status failed."
    $status = $statusOutput | ConvertFrom-Json
    Assert-True ([bool]$status.usable_installed_state) `
        "Installed release status is not usable."
    Assert-True ([int]$status.menu_leaf_count -eq 1) `
        "Installed release does not expose exactly one package leaf."
    Assert-True ([int]$status.menu_action_count -eq 2) `
        "Installed release does not expose two Skill Magnet actions."
    Assert-True ([int]$status.library_manager_entry_count -eq 1) `
        "Installed release does not expose one Skill Library Manager action."

    $rollbackOutput = python -m skill_magnet rollback-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu update rollback failed."
    $rollback = $rollbackOutput | ConvertFrom-Json
    Assert-True ([bool]$rollback.rollback_point_removed) `
        "Rollback point was not removed."
    Assert-True ([bool](Get-AppxPackage -Name "SkillMagnet.ContextMenu")) `
        "Rollback did not restore the prior real Appx package."
    $restoredMenuHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath (Join-Path $installRoot "SkillMagnetMenu.tsv")).Hash
    Assert-True ($restoredMenuHash -eq $priorMenuHash) `
        "Rollback did not restore the prior menu contract."
    $priorStatusOutput = python -m skill_magnet --config $priorConfig `
        context-menu-status --platform windows | Out-String
    $priorStatus = $priorStatusOutput | ConvertFrom-Json
    Assert-True ([bool]$priorStatus.usable_installed_state) `
        "Rollback did not restore a usable prior release."

    $uninstallOutput = python -m skill_magnet uninstall-context-menu `
        --platform windows --confirm | Out-String
    Assert-True ($LASTEXITCODE -eq 0) "Context-menu uninstall failed."
    $uninstalled = $uninstallOutput | ConvertFrom-Json
    Assert-True ([bool]$uninstalled.rollback_point_removed) `
        "Uninstall left a rollback point."

    Assert-True (-not (Get-AppxPackage -Name "SkillMagnet.ContextMenu")) `
        "MSIX package remains installed."
    foreach ($ownedStore in @(
        @{ Path = "Cert:\CurrentUser\My\$thumbprint"; Owned = [bool]$state.created_my },
        @{ Path = "Cert:\CurrentUser\TrustedPeople\$thumbprint"; Owned = [bool]$state.created_trusted_people },
        @{ Path = "Cert:\LocalMachine\TrustedPeople\$thumbprint"; Owned = [bool]$state.created_machine_trusted_people }
    )) {
        if ($ownedStore.Owned) {
            Assert-True (-not (Test-Path -LiteralPath $ownedStore.Path)) `
                "Owned certificate remains in $($ownedStore.Path)."
        }
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
        if ($isElevated) {
            Remove-Item -LiteralPath "Cert:\LocalMachine\TrustedPeople\$thumbprint" `
                -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($legacyThumbprint in $legacyThumbprints) {
        Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $legacyThumbprint) `
            -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ("Cert:\CurrentUser\TrustedPeople\" + $legacyThumbprint) `
            -Force -ErrorAction SilentlyContinue
        if ($isElevated) {
            Remove-Item -LiteralPath ("Cert:\LocalMachine\TrustedPeople\" + $legacyThumbprint) `
                -Force -ErrorAction SilentlyContinue
        }
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
    if ($lifecycleTranscript) {
        Stop-Transcript | Out-Null
    }
}

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("install", "uninstall", "status", "cleanup-certificate")]
    [string]$Action,
    [string]$Manifest,
    [string]$ExternalLocation
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "certificate-state.ps1")
Import-Module Microsoft.PowerShell.Security
Import-Module PKI
$name = "SkillMagnet.ContextMenu"
$nonInteractiveCertificateTrust =
    $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST -eq "1"
$legacyThumbprints = @()

if ($Action -eq "install") {
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Missing package manifest: $Manifest" }
    if (-not (Test-Path -LiteralPath $ExternalLocation -PathType Container)) { throw "Missing external location: $ExternalLocation" }
    $package = Join-Path $ExternalLocation "SkillMagnet.ContextMenu.msix"
    if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { throw "Missing signed identity package: $package" }
    $certificateState = Join-Path $ExternalLocation "certificate-state.json"
    if (Test-Path -LiteralPath $certificateState -PathType Leaf) {
        $state = Get-Content -LiteralPath $certificateState -Raw | ConvertFrom-Json
        $machineCertificate = "Cert:\LocalMachine\TrustedPeople\" + $state.thumbprint
        if (-not (Test-Path -LiteralPath $machineCertificate)) {
            $publicCertificate = Join-Path $ExternalLocation "SkillMagnet.cer"
            if (-not (Test-Path -LiteralPath $publicCertificate -PathType Leaf)) {
                throw "Missing package signing certificate: $publicCertificate"
            }
            if ($nonInteractiveCertificateTrust) {
                Import-Certificate -FilePath $publicCertificate `
                    -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
                $trustExitCode = 0
            }
            else {
                $trust = Start-Process -FilePath certutil.exe -Verb RunAs -Wait -PassThru `
                    -ArgumentList @("-addstore", "TrustedPeople", $publicCertificate)
                $trustExitCode = $trust.ExitCode
            }
            if ($trustExitCode -ne 0 -or -not (Test-Path -LiteralPath $machineCertificate)) {
                throw "Package signing certificate was not trusted"
            }
            $state | Add-Member -NotePropertyName created_machine_trusted_people -NotePropertyValue $true -Force
            $state | ConvertTo-Json | Set-Content -LiteralPath $certificateState -Encoding UTF8
        }

        $legacyThumbprints = @(
            Get-SkillMagnetOwnedLegacyTrustedCertificateThumbprints `
                -State $state `
                -ActiveThumbprint ([string]$state.thumbprint)
        )
        if ($legacyThumbprints.Count -gt 0) {
            $machinePaths = @(
                $legacyThumbprints | ForEach-Object {
                    "Cert:\LocalMachine\TrustedPeople\$_"
                }
            )
            if ($nonInteractiveCertificateTrust) {
                foreach ($machinePath in $machinePaths) {
                    Remove-Item -LiteralPath $machinePath -Force
                }
                $cleanupExitCode = 0
            }
            else {
                $pathLiterals = ($machinePaths | ForEach-Object {
                    "'" + $_.Replace("'", "''") + "'"
                }) -join ","
                $cleanupCommand = (
                    "@($pathLiterals) | ForEach-Object { " +
                    "if (Test-Path -LiteralPath `$_) { Remove-Item -LiteralPath `$_ -Force } }"
                )
                $encodedCommand = [Convert]::ToBase64String(
                    [Text.Encoding]::Unicode.GetBytes($cleanupCommand)
                )
                $cleanup = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru `
                    -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $encodedCommand)
                $cleanupExitCode = $cleanup.ExitCode
            }
            $machineResidue = @($machinePaths | Where-Object {
                Test-Path -LiteralPath $_
            })
            if ($cleanupExitCode -ne 0 -or $machineResidue.Count -gt 0) {
                throw "Legacy Skill Magnet machine certificate cleanup failed"
            }
            foreach ($thumbprint in $legacyThumbprints) {
                Remove-Item -LiteralPath (
                    "Cert:\CurrentUser\TrustedPeople\" + $thumbprint
                ) -Force
            }
        }
    }
    Add-AppxPackage -Path $package -ForceApplicationShutdown
}
elseif ($Action -eq "uninstall") {
    Get-AppxPackage -Name $name | Remove-AppxPackage
}
elseif ($Action -eq "cleanup-certificate") {
    $statePath = Join-Path $ExternalLocation "certificate-state.json"
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $machineCertificate = "Cert:\LocalMachine\TrustedPeople\" + $state.thumbprint
        if ($state.created_machine_trusted_people -and (Test-Path -LiteralPath $machineCertificate)) {
            if ($nonInteractiveCertificateTrust) {
                Remove-Item -LiteralPath $machineCertificate -Force
            }
            else {
                Start-Process -FilePath certutil.exe -Verb RunAs -Wait `
                    -ArgumentList @("-delstore", "TrustedPeople", $state.thumbprint)
            }
        }
        if ($state.created_root) { & certutil.exe -user -delstore Root $state.thumbprint | Out-Null }
        if ($state.created_trusted_people) { Remove-Item -LiteralPath ("Cert:\CurrentUser\TrustedPeople\" + $state.thumbprint) -ErrorAction SilentlyContinue }
        if ($state.created_my) { & certutil.exe -user -delstore My $state.thumbprint | Out-Null }
    }
}

$package = Get-AppxPackage -Name $name | Select-Object -First 1
[ordered]@{
    installed = ($null -ne $package)
    name = $name
    package_full_name = if ($package) { $package.PackageFullName } else { $null }
    install_location = if ($package) { $package.InstallLocation } else { $null }
    legacy_certificate_thumbprints_removed = @($legacyThumbprints)
} | ConvertTo-Json -Compress

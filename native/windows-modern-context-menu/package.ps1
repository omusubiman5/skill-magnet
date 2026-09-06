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
$expectedPublisher = "CN=Skill Magnet Local"
$expectedVersion = "0.5.9.0"
$expectedArchitecture = "X64"
$nonInteractiveCertificateTrust =
    $env:SKILL_MAGNET_NONINTERACTIVE_CERTIFICATE_TRUST -eq "1"
$legacyThumbprints = @()
$legacyThumbprintsPreserved = @()

if ($Action -eq "install") {
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Missing package manifest: $Manifest" }
    if (-not (Test-Path -LiteralPath $ExternalLocation -PathType Container)) { throw "Missing external location: $ExternalLocation" }
    $package = Join-Path $ExternalLocation "SkillMagnet.ContextMenu.msix"
    if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { throw "Missing signed identity package: $package" }
    $certificateState = Join-Path $ExternalLocation "certificate-state.json"
    if (Test-Path -LiteralPath $certificateState -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $certificateState -Raw | ConvertFrom-Json
            $state = Assert-SkillMagnetCertificateState -State $state
            Get-SkillMagnetCertificateDeletionOwnership -State $state | Out-Null
        }
        catch {
            throw (
                "Certificate ownership state is invalid or unverifiable; " +
                "no certificate was changed. " + $_.Exception.Message
            )
        }
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
        $declaredLegacyThumbprints = @(
            @($state.owned_certificate_thumbprints) |
                ForEach-Object { ([string]$_).ToUpperInvariant() } |
                Where-Object {
                    $_ -ne ([string]$state.thumbprint).ToUpperInvariant()
                } |
                Sort-Object -Unique
        )
        $legacyThumbprintsPreserved = @(
            $declaredLegacyThumbprints | Where-Object {
                $candidate = $_
                $existsInManagedStore =
                    (Test-Path -LiteralPath "Cert:\CurrentUser\My\$candidate") -or
                    (Test-Path -LiteralPath "Cert:\CurrentUser\TrustedPeople\$candidate") -or
                    (Test-Path -LiteralPath "Cert:\LocalMachine\TrustedPeople\$candidate")
                $existsInManagedStore -and $legacyThumbprints -notcontains $candidate
            }
        )
        if ($legacyThumbprints.Count -gt 0) {
            $machinePaths = @(
                $legacyThumbprints | ForEach-Object {
                    "Cert:\LocalMachine\TrustedPeople\$_"
                }
            )
            if ($nonInteractiveCertificateTrust) {
                foreach ($machinePath in $machinePaths) {
                    if (Test-Path -LiteralPath $machinePath) {
                        Remove-Item -LiteralPath $machinePath -Force
                    }
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
                $userPath = "Cert:\CurrentUser\TrustedPeople\" + $thumbprint
                if (Test-Path -LiteralPath $userPath) {
                    Remove-Item -LiteralPath $userPath -Force
                }
            }
        }
    }
    Add-AppxPackage -Path $package -ForceApplicationShutdown
}
elseif ($Action -eq "uninstall") {
    # Remove only packages owned by this product identity.  A colliding package
    # name with another publisher is reported by status and must not be deleted.
    Get-AppxPackage -Name $name |
        Where-Object { $_.Publisher -eq $expectedPublisher } |
        Remove-AppxPackage
}
elseif ($Action -eq "cleanup-certificate") {
    $statePath = Join-Path $ExternalLocation "certificate-state.json"
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $state = Assert-SkillMagnetCertificateState -State $state
            $deletionRequested =
                [bool]$state.created_machine_trusted_people -or
                [bool]$state.created_root -or
                [bool]$state.created_trusted_people -or
                [bool]$state.created_my
            if ($deletionRequested) {
                Get-SkillMagnetCertificateDeletionOwnership -State $state | Out-Null
            }
        }
        catch {
            throw (
                "Certificate cleanup refused; all certificates were preserved. " +
                $_.Exception.Message
            )
        }
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

$sameNamePackages = @(Get-AppxPackage -Name $name)
$expectedPackages = @(
    $sameNamePackages | Where-Object {
        $_.Name -eq $name -and
        $_.Publisher -eq $expectedPublisher -and
        $_.Version.ToString() -eq $expectedVersion -and
        $_.Architecture.ToString().ToUpperInvariant() -eq $expectedArchitecture
    }
)
$package = $expectedPackages | Select-Object -First 1
$unexpectedSameNamePackages = @(
    $sameNamePackages | Where-Object {
        -not (
            $_.Name -eq $name -and
            $_.Publisher -eq $expectedPublisher -and
            $_.Version.ToString() -eq $expectedVersion -and
            $_.Architecture.ToString().ToUpperInvariant() -eq $expectedArchitecture
        )
    }
)
[ordered]@{
    installed = ($null -ne $package)
    name = if ($package) { $package.Name } else { $name }
    version = if ($package) { $package.Version.ToString() } else { $null }
    architecture = if ($package) { $package.Architecture.ToString() } else { $null }
    publisher = if ($package) { $package.Publisher } else { $null }
    package_full_name = if ($package) { $package.PackageFullName } else { $null }
    install_location = if ($package) { $package.InstallLocation } else { $null }
    same_name_package_count = $sameNamePackages.Count
    expected_identity_match_count = $expectedPackages.Count
    unexpected_same_name_package_count = $unexpectedSameNamePackages.Count
    unexpected_same_name_package_full_names = @(
        $unexpectedSameNamePackages | ForEach-Object { $_.PackageFullName }
    )
    same_name_packages = @(
        $sameNamePackages | ForEach-Object {
            [ordered]@{
                name = $_.Name
                version = $_.Version.ToString()
                architecture = $_.Architecture.ToString()
                publisher = $_.Publisher
                package_full_name = $_.PackageFullName
            }
        }
    )
    legacy_certificate_thumbprints_removed = @($legacyThumbprints)
    legacy_certificate_thumbprints_preserved = @($legacyThumbprintsPreserved)
} | ConvertTo-Json -Compress

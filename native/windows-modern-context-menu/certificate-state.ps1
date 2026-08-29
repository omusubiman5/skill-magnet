function Merge-SkillMagnetCertificateState {
    param(
        [Parameter(Mandatory=$false)]
        [object]$PreviousState,
        [Parameter(Mandatory=$true)]
        [string]$Thumbprint,
        [Parameter(Mandatory=$true)]
        [bool]$CreatedMy,
        [Parameter(Mandatory=$true)]
        [bool]$CreatedTrustedPeople
    )

    $sameCertificate = $PreviousState -and (
        $PreviousState.thumbprint -eq $Thumbprint
    )
    [ordered]@{
        thumbprint = $Thumbprint
        created_my = $CreatedMy -or (
            $sameCertificate -and [bool]$PreviousState.created_my
        )
        created_trusted_people = $CreatedTrustedPeople -or (
            $sameCertificate -and [bool]$PreviousState.created_trusted_people
        )
        created_machine_trusted_people = (
            $sameCertificate -and [bool]$PreviousState.created_machine_trusted_people
        )
    }
}

function Test-SkillMagnetLegacyTrustedCertificate {
    param(
        [Parameter(Mandatory=$true)]
        [object]$Certificate,
        [Parameter(Mandatory=$true)]
        [string]$ActiveThumbprint
    )

    $thumbprint = [string]$Certificate.Thumbprint
    if ($thumbprint -notmatch '^[0-9A-Fa-f]{40}$' -or $thumbprint -eq $ActiveThumbprint) {
        return $false
    }
    if ($Certificate.Subject -ne "CN=Skill Magnet Local" -or
        $Certificate.Issuer -ne "CN=Skill Magnet Local") {
        return $false
    }
    $ekuExtension = $Certificate.Extensions |
        Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
        Select-Object -First 1
    if (-not $ekuExtension) { return $false }
    $eku = New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension(
        $ekuExtension,
        $false
    )
    return [bool]($eku.EnhancedKeyUsages | Where-Object {
        $_.Value -eq "1.3.6.1.5.5.7.3.3"
    })
}

function Get-SkillMagnetLegacyTrustedCertificateThumbprints {
    param(
        [Parameter(Mandatory=$true)]
        [string]$ActiveThumbprint
    )

    if ($ActiveThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "Active Skill Magnet certificate thumbprint is invalid."
    }
    $userCertificates = @(
        Get-ChildItem Cert:\CurrentUser\TrustedPeople |
            Where-Object {
                Test-SkillMagnetLegacyTrustedCertificate `
                    -Certificate $_ -ActiveThumbprint $ActiveThumbprint
            }
    )
    $machineThumbprints = @(
        Get-ChildItem Cert:\LocalMachine\TrustedPeople |
            Where-Object {
                Test-SkillMagnetLegacyTrustedCertificate `
                    -Certificate $_ -ActiveThumbprint $ActiveThumbprint
            } |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() }
    )
    return @(
        $userCertificates |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Where-Object { $machineThumbprints -contains $_ } |
            Sort-Object -Unique
    )
}

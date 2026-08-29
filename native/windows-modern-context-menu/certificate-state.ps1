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
    $ownedThumbprints = @()
    if ($PreviousState) {
        if ($PreviousState.PSObject.Properties.Name -contains "owned_certificate_thumbprints") {
            $ownedThumbprints += @($PreviousState.owned_certificate_thumbprints)
        }
        $previousThumbprint = [string]$PreviousState.thumbprint
        $previousOwned = [bool]$PreviousState.created_my -or
            [bool]$PreviousState.created_trusted_people -or
            [bool]$PreviousState.created_machine_trusted_people
        if ($previousOwned -and $previousThumbprint -match '^[0-9A-Fa-f]{40}$') {
            $ownedThumbprints += $previousThumbprint
        }
    }
    if ($CreatedMy -or $CreatedTrustedPeople) {
        $ownedThumbprints += $Thumbprint
    }
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
        owned_certificate_thumbprints = @(
            $ownedThumbprints |
                Where-Object { $_ -match '^[0-9A-Fa-f]{40}$' } |
                ForEach-Object { $_.ToUpperInvariant() } |
                Sort-Object -Unique
        )
    }
}

function Test-SkillMagnetOwnedLegacyTrustedCertificate {
    param(
        [Parameter(Mandatory=$true)]
        [object]$Certificate,
        [Parameter(Mandatory=$true)]
        [string]$ActiveThumbprint,
        [Parameter(Mandatory=$true)]
        [string[]]$OwnedThumbprints
    )

    $thumbprint = ([string]$Certificate.Thumbprint).ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$' -or
        $thumbprint -eq $ActiveThumbprint.ToUpperInvariant() -or
        @($OwnedThumbprints | ForEach-Object { $_.ToUpperInvariant() }) -notcontains $thumbprint) {
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

function Get-SkillMagnetOwnedLegacyTrustedCertificateThumbprints {
    param(
        [Parameter(Mandatory=$true)]
        [object]$State,
        [Parameter(Mandatory=$true)]
        [string]$ActiveThumbprint
    )

    if ($ActiveThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "Active Skill Magnet certificate thumbprint is invalid."
    }
    $ownedThumbprints = if (
        $State -and
        $State.PSObject.Properties.Name -contains "owned_certificate_thumbprints"
    ) { @($State.owned_certificate_thumbprints) } else { @() }
    $result = @()
    foreach ($ownedThumbprint in $ownedThumbprints) {
        $candidate = ([string]$ownedThumbprint).ToUpperInvariant()
        if ($candidate -notmatch '^[0-9A-F]{40}$' -or
            $candidate -eq $ActiveThumbprint.ToUpperInvariant()) {
            continue
        }
        $userPath = "Cert:\CurrentUser\TrustedPeople\$candidate"
        $machinePath = "Cert:\LocalMachine\TrustedPeople\$candidate"
        if (-not (Test-Path -LiteralPath $userPath) -or
            -not (Test-Path -LiteralPath $machinePath)) {
            continue
        }
        $userCertificate = Get-Item -LiteralPath $userPath
        if (Test-SkillMagnetOwnedLegacyTrustedCertificate `
            -Certificate $userCertificate `
            -ActiveThumbprint $ActiveThumbprint `
            -OwnedThumbprints $ownedThumbprints) {
            $result += $candidate
        }
    }
    return @($result | Sort-Object -Unique)
}

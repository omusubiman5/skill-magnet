Set-Variable -Name SkillMagnetCertificateSubject `
    -Value "CN=Skill Magnet Local" -Option ReadOnly -Scope Script -Force
Set-Variable -Name SkillMagnetCertificateFriendlyName `
    -Value "Skill Magnet local package signing" -Option ReadOnly -Scope Script -Force
Set-Variable -Name SkillMagnetCodeSigningEku `
    -Value "1.3.6.1.5.5.7.3.3" -Option ReadOnly -Scope Script -Force

function Test-SkillMagnetProductCertificate {
    param(
        [Parameter(Mandatory=$true)]
        [object]$Certificate,
        [Parameter(Mandatory=$false)]
        [string]$ExpectedThumbprint = ""
    )

    $thumbprint = ([string]$Certificate.Thumbprint).ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') { return $false }
    if ($ExpectedThumbprint) {
        if ($ExpectedThumbprint -notmatch '^[0-9A-Fa-f]{40}$' -or
            $thumbprint -ne $ExpectedThumbprint.ToUpperInvariant()) {
            return $false
        }
    }
    if ($Certificate.Subject -ne $script:SkillMagnetCertificateSubject -or
        $Certificate.Issuer -ne $script:SkillMagnetCertificateSubject -or
        -not [bool]$Certificate.HasPrivateKey -or
        [string]$Certificate.FriendlyName -ne $script:SkillMagnetCertificateFriendlyName) {
        return $false
    }
    $ekuExtension = $Certificate.Extensions |
        Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
        Select-Object -First 1
    if (-not $ekuExtension) { return $false }
    try {
        $eku = New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension(
            $ekuExtension,
            $false
        )
    }
    catch {
        return $false
    }
    return [bool]($eku.EnhancedKeyUsages | Where-Object {
        $_.Value -eq $script:SkillMagnetCodeSigningEku
    })
}

function Assert-SkillMagnetCertificateState {
    param(
        [Parameter(Mandatory=$true)]
        [object]$State
    )

    if (-not $State) {
        throw "Skill Magnet certificate ownership state is missing."
    }
    foreach ($property in @(
        "thumbprint",
        "created_my",
        "created_trusted_people",
        "created_machine_trusted_people"
    )) {
        if ($State.PSObject.Properties.Name -notcontains $property) {
            throw "Skill Magnet certificate ownership state is missing '$property'."
        }
    }
    if ([string]$State.thumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "Skill Magnet certificate ownership thumbprint is invalid."
    }
    foreach ($flag in @(
        "created_my",
        "created_trusted_people",
        "created_machine_trusted_people"
    )) {
        if ($State.$flag -isnot [bool]) {
            throw "Skill Magnet certificate ownership flag '$flag' is invalid."
        }
    }
    if ($State.PSObject.Properties.Name -contains "owned_certificate_thumbprints") {
        foreach ($ownedThumbprint in @($State.owned_certificate_thumbprints)) {
            if ([string]$ownedThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
                throw "Skill Magnet legacy certificate ownership thumbprint is invalid."
            }
        }
    }
    return $State
}

function Assert-SkillMagnetCertificateDeletionOwnership {
    param(
        [Parameter(Mandatory=$true)]
        [object]$State,
        [Parameter(Mandatory=$true)]
        [object]$CurrentUserMyCertificate
    )

    $validatedState = Assert-SkillMagnetCertificateState -State $State
    if (-not (Test-SkillMagnetProductCertificate `
        -Certificate $CurrentUserMyCertificate `
        -ExpectedThumbprint ([string]$validatedState.thumbprint))) {
        throw (
            "Certificate ownership could not be verified; " +
            "all CurrentUser and LocalMachine certificates were preserved."
        )
    }
    return $validatedState
}

function Get-SkillMagnetCertificateDeletionOwnership {
    param(
        [Parameter(Mandatory=$true)]
        [object]$State
    )

    $validatedState = Assert-SkillMagnetCertificateState -State $State
    $thumbprint = ([string]$validatedState.thumbprint).ToUpperInvariant()
    $myPath = "Cert:\CurrentUser\My\$thumbprint"
    if (-not (Test-Path -LiteralPath $myPath)) {
        throw (
            "Certificate ownership could not be verified because the matching " +
            "CurrentUser\\My certificate is missing; all certificates were preserved."
        )
    }
    $certificate = Get-Item -LiteralPath $myPath
    Assert-SkillMagnetCertificateDeletionOwnership `
        -State $validatedState `
        -CurrentUserMyCertificate $certificate | Out-Null
    return $certificate
}

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
        $myPath = "Cert:\CurrentUser\My\$candidate"
        if (-not (Test-Path -LiteralPath $userPath) -or
            -not (Test-Path -LiteralPath $machinePath) -or
            -not (Test-Path -LiteralPath $myPath)) {
            continue
        }
        $userCertificate = Get-Item -LiteralPath $userPath
        $myCertificate = Get-Item -LiteralPath $myPath
        if ((Test-SkillMagnetOwnedLegacyTrustedCertificate `
            -Certificate $userCertificate `
            -ActiveThumbprint $ActiveThumbprint `
            -OwnedThumbprints $ownedThumbprints) -and
            (Test-SkillMagnetProductCertificate `
                -Certificate $myCertificate `
                -ExpectedThumbprint $candidate)) {
            $result += $candidate
        }
    }
    return @($result | Sort-Object -Unique)
}

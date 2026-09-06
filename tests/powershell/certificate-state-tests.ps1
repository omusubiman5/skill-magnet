$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\..\native\windows-modern-context-menu\certificate-state.ps1")

$owned = [pscustomobject]@{
    thumbprint = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    created_my = $true
    created_trusted_people = $true
    created_machine_trusted_people = $true
}
$updated = Merge-SkillMagnetCertificateState `
    -PreviousState $owned `
    -Thumbprint $owned.thumbprint `
    -CreatedMy $false `
    -CreatedTrustedPeople $false
if (-not $updated.created_my) { throw "created_my ownership was lost" }
if (-not $updated.created_trusted_people) { throw "created_trusted_people ownership was lost" }
if (-not $updated.created_machine_trusted_people) { throw "machine trust ownership was lost" }
if (@($updated.owned_certificate_thumbprints) -notcontains $owned.thumbprint) {
    throw "owned certificate history was lost"
}

$replacement = Merge-SkillMagnetCertificateState `
    -PreviousState $owned `
    -Thumbprint "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" `
    -CreatedMy $false `
    -CreatedTrustedPeople $false
if ($replacement.created_my -or $replacement.created_trusted_people -or $replacement.created_machine_trusted_people) {
    throw "ownership leaked to a replacement certificate"
}
if (@($replacement.owned_certificate_thumbprints) -notcontains $owned.thumbprint) {
    throw "replacement did not retain explicit legacy ownership"
}

$active = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
$legacyEkus = New-Object System.Security.Cryptography.OidCollection
$legacyEkus.Add(
    (New-Object System.Security.Cryptography.Oid("1.3.6.1.5.5.7.3.3"))
) | Out-Null
$validLegacy = New-Object psobject -Property @{
    Thumbprint = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    Subject = "CN=Skill Magnet Local"
    Issuer = "CN=Skill Magnet Local"
    Extensions = @(
        New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension(
            $legacyEkus,
            $false
        )
    )
}
if (-not (Test-SkillMagnetOwnedLegacyTrustedCertificate `
    -Certificate $validLegacy -ActiveThumbprint $active `
    -OwnedThumbprints @($validLegacy.Thumbprint))) {
    throw "owned legacy code-signing certificate was not recognized"
}
$unownedLookalike = $validLegacy.PSObject.Copy()
$unownedLookalike.Thumbprint = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
if (Test-SkillMagnetOwnedLegacyTrustedCertificate `
    -Certificate $unownedLookalike -ActiveThumbprint $active `
    -OwnedThumbprints @($validLegacy.Thumbprint)) {
    throw "unowned lookalike certificate was classified as Skill Magnet-owned"
}
$wrongSubject = $validLegacy.PSObject.Copy()
$wrongSubject.Subject = "CN=Unrelated"
if (Test-SkillMagnetOwnedLegacyTrustedCertificate `
    -Certificate $wrongSubject -ActiveThumbprint $active `
    -OwnedThumbprints @($wrongSubject.Thumbprint)) {
    throw "unrelated certificate was classified as Skill Magnet-owned"
}
$activeCertificate = $validLegacy.PSObject.Copy()
$activeCertificate.Thumbprint = $active
if (Test-SkillMagnetOwnedLegacyTrustedCertificate `
    -Certificate $activeCertificate -ActiveThumbprint $active `
    -OwnedThumbprints @($activeCertificate.Thumbprint)) {
    throw "active certificate was classified as legacy"
}

$validProduct = $validLegacy.PSObject.Copy()
$validProduct | Add-Member -NotePropertyName HasPrivateKey -NotePropertyValue $true
$validProduct | Add-Member `
    -NotePropertyName FriendlyName `
    -NotePropertyValue "Skill Magnet local package signing"
$validState = [pscustomobject]@{
    thumbprint = $validProduct.Thumbprint
    created_my = $true
    created_trusted_people = $true
    created_machine_trusted_people = $true
    owned_certificate_thumbprints = @($validProduct.Thumbprint)
}
Assert-SkillMagnetCertificateDeletionOwnership `
    -State $validState `
    -CurrentUserMyCertificate $validProduct | Out-Null

$unrelatedProduct = $validProduct.PSObject.Copy()
$unrelatedProduct.Subject = "CN=Unrelated"
$unrelatedRefused = $false
try {
    Assert-SkillMagnetCertificateDeletionOwnership `
        -State $validState `
        -CurrentUserMyCertificate $unrelatedProduct | Out-Null
}
catch {
    $unrelatedRefused = $_.Exception.Message -match "preserved"
}
if (-not $unrelatedRefused) {
    throw "tampered state targeting an unrelated certificate was not refused"
}

$malformedState = $validState.PSObject.Copy()
$malformedState.thumbprint = "Cert:\CurrentUser\My\unrelated"
$malformedRefused = $false
try {
    Assert-SkillMagnetCertificateState -State $malformedState | Out-Null
}
catch {
    $malformedRefused = $_.Exception.Message -match "thumbprint is invalid"
}
if (-not $malformedRefused) {
    throw "malformed certificate state was not refused"
}

$stringFlagState = $validState.PSObject.Copy()
$stringFlagState.created_my = "false"
$stringFlagRefused = $false
try {
    Assert-SkillMagnetCertificateState -State $stringFlagState | Out-Null
}
catch {
    $stringFlagRefused = $_.Exception.Message -match "flag 'created_my' is invalid"
}
if (-not $stringFlagRefused) {
    throw "string certificate ownership flag was not refused"
}
Write-Output "certificate-state-tests: OK"

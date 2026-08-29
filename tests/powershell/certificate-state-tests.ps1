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

$replacement = Merge-SkillMagnetCertificateState `
    -PreviousState $owned `
    -Thumbprint "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" `
    -CreatedMy $false `
    -CreatedTrustedPeople $false
if ($replacement.created_my -or $replacement.created_trusted_people -or $replacement.created_machine_trusted_people) {
    throw "ownership leaked to a replacement certificate"
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
if (-not (Test-SkillMagnetLegacyTrustedCertificate -Certificate $validLegacy -ActiveThumbprint $active)) {
    throw "owned legacy code-signing certificate was not recognized"
}
$wrongSubject = $validLegacy.PSObject.Copy()
$wrongSubject.Subject = "CN=Unrelated"
if (Test-SkillMagnetLegacyTrustedCertificate -Certificate $wrongSubject -ActiveThumbprint $active) {
    throw "unrelated certificate was classified as Skill Magnet-owned"
}
$activeCertificate = $validLegacy.PSObject.Copy()
$activeCertificate.Thumbprint = $active
if (Test-SkillMagnetLegacyTrustedCertificate -Certificate $activeCertificate -ActiveThumbprint $active) {
    throw "active certificate was classified as legacy"
}
Write-Output "certificate-state-tests: OK"

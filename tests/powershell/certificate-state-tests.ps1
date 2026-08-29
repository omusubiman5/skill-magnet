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
Write-Output "certificate-state-tests: OK"

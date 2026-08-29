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

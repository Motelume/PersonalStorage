param([Parameter(Mandatory=$true)][string]$Artifact,[Parameter(Mandatory=$true)][string]$CertificateThumbprint)
$ErrorActionPreference='Stop'
$resolved=(Resolve-Path -LiteralPath $Artifact).Path
$certificate=Get-Item -LiteralPath ('Cert:\CurrentUser\My\'+$CertificateThumbprint)
if(-not $certificate.HasPrivateKey){throw 'Signing certificate must have its private key'}
if(-not ($certificate.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3')){throw 'A code-signing certificate is required'}
$result=Set-AuthenticodeSignature -LiteralPath $resolved -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer 'http://timestamp.digicert.com'
if($result.Status -ne 'Valid'){throw ('Signature validation failed: '+$result.Status)}
$result | Select-Object Path,Status

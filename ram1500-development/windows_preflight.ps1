# Read-only Windows workstation preflight for Comma/Claude diagnostics.
# Does not create keys, connect an SSH shell, or install/change Comma software.
# Example: powershell -NoProfile -ExecutionPolicy Bypass -File .\ram1500-development\windows_preflight.ps1 -DeviceIP 192.168.1.100
[CmdletBinding()]
param(
  [ValidatePattern('^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$')]
  [string]$DeviceIP = ''
)
$ErrorActionPreference = "Stop"
$checks = [ordered]@{}
$checks.timestamp = (Get-Date).ToUniversalTime().ToString("o")
$checks.windows = [Environment]::OSVersion.VersionString
$checks.gitAvailable = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
$checks.sshAvailable = $null -ne (Get-Command ssh -ErrorAction SilentlyContinue)
$checks.sshKeygenAvailable = $null -ne (Get-Command ssh-keygen -ErrorAction SilentlyContinue)
$checks.pubKeyPresent = $false
$checks.privateKeysInspected = $false
$checks.commaTcp22Reachable = $null
$checks.deviceIpProvided = [bool]$DeviceIP

# Only inspect file names; never read or display secret private keys.
$sshDir = Join-Path $env:USERPROFILE ".ssh"
if (Test-Path -LiteralPath $sshDir) {
  $pubs = @(Get-ChildItem -LiteralPath $sshDir -Filter '*.pub' -File -ErrorAction SilentlyContinue)
  $checks.pubKeyPresent = $pubs.Count -gt 0
}
if ($DeviceIP) {
  $parts = $DeviceIP.Split('.')
  if (($parts | Where-Object { [int]$_ -gt 255 }).Count -gt 0) {
    throw "Invalid IPv4 address"
  }
  $checks.commaTcp22Reachable = [bool](Test-NetConnection -ComputerName $DeviceIP -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)
}
$checks["guidance"] = @(
 "On Comma while parked: enable SSH and set GitHub username wholesalebridgeowner.",
 "Generate a dedicated SSH key pair on your trusted PC only after you approve; add the PUBLIC key to your GitHub account, keep the private key locally.",
 "Confirm the device host key fingerprint with a trusted channel before accepting any SSH connection.",
 "Run an initial read-only connection only; do not alter /data/openpilot or Panda firmware.",
 "Private CAN telemetry should never be committed to GitHub."
)
[pscustomobject]$checks | ConvertTo-Json -Depth 4

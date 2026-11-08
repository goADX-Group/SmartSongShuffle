$bt = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'OK' -and $_.FriendlyName -match 'head|ear|buds|audio|sound|sony|airpods|jbl|bose|anker' }
$audio = Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'OK' }
Write-Output "--- Bluetooth audio devices (OK) ---"
if ($bt) { $bt | Select-Object FriendlyName, Status | Format-Table -AutoSize } else { Write-Output "none" }
Write-Output "--- Active audio endpoints ---"
$audio | Select-Object FriendlyName | Format-Table -AutoSize

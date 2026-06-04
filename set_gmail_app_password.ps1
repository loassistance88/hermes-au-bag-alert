param(
    [string]$EnvPath = "$PSScriptRoot\.env"
)

if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw ".env was not found at $EnvPath"
}

$secure = Read-Host "Paste the 16-character Gmail App Password" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$password = ($password -replace '\s+', '').Trim()
if ($password.Length -lt 16) {
    throw "That does not look like a Gmail App Password. It should be 16 characters, ignoring spaces."
}

$lines = Get-Content -LiteralPath $EnvPath -Encoding UTF8
$updated = $false
$lines = $lines | ForEach-Object {
    if ($_ -match '^SMTP_PASSWORD=') {
        $updated = $true
        "SMTP_PASSWORD=$password"
    } else {
        $_
    }
}

if (-not $updated) {
    $lines += "SMTP_PASSWORD=$password"
}

Set-Content -LiteralPath $EnvPath -Value $lines -Encoding UTF8
Write-Host "Saved Gmail App Password to $EnvPath"
Write-Host "Now run: powershell -ExecutionPolicy Bypass -File .\run_once.ps1 --test-email"

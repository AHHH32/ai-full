param([switch]$FirstOnly)
$ErrorActionPreference = 'Stop'
$rootPath = Split-Path -Parent $PSScriptRoot
$audioPath = Join-Path $rootPath 'apps/web/public/assets/audio'
New-Item -ItemType Directory -Path $audioPath -Force | Out-Null
$lines = Get-Content -LiteralPath (Join-Path $rootPath 'tmp/mock-lines.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
$token = New-Object -ComObject SAPI.SpObjectToken
$token.SetId('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\MSTTS_V110_zhCN_HuihuiM')
$voice.Voice = $token
$voice.Rate = 0
foreach ($line in $lines.PSObject.Properties) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    try {
        $stream.Format.Type = 22
        $stream.Open((Join-Path $audioPath ($line.Name + '.wav')), 3, $false)
        $voice.AudioOutputStream = $stream
        $voice.Speak($line.Value, 0) | Out-Null
    } finally {
        $stream.Close()
    }
    Write-Output ('Created ' + $line.Name)
    if ($FirstOnly) { break }
}

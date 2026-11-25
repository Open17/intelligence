<#
.SYNOPSIS
    Download the model files echo needs into .\models (Windows version of
    download-models.sh).

.EXAMPLE
    .\scripts\download-models.ps1                         # Silero VAD + base.en
    .\scripts\download-models.ps1 -Whisper tiny.en-q5_1   # pick whisper model(s)
    .\scripts\download-models.ps1 -Grammar                # also the Qwen2.5 grammar model
    .\scripts\download-models.ps1 -All

.NOTES
    Whisper model names: tiny.en, tiny.en-q5_1, tiny.en-q8_0, base.en,
    base.en-q5_1, base.en-q8_0, small.en, small.en-q5_1, small.en-q8_0.
    Files that already exist are skipped.
#>
param(
    [string[]] $Whisper = @(),
    [switch]   $Grammar,
    [switch]   $All
)

$ErrorActionPreference = "Stop"
# The progress bar makes Invoke-WebRequest very slow on Windows PowerShell 5.
$ProgressPreference = "SilentlyContinue"

$WhisperBaseUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
$SileroUrl      = "https://github.com/snakers4/silero-vad/raw/v6.2.3/src/silero_vad/data/silero_vad.onnx"
$GrammarUrl     = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

$ModelsDir = Join-Path $PSScriptRoot "..\models"
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
$ModelsDir = (Resolve-Path $ModelsDir).Path

if ($All) {
    $Whisper = @("tiny.en", "tiny.en-q5_1", "base.en", "base.en-q5_1")
    $Grammar = $true
}
if ($Whisper.Count -eq 0) { $Whisper = @("base.en") }

function Get-Model([string] $Url, [string] $FileName) {
    $dest = Join-Path $ModelsDir $FileName
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 0)) {
        Write-Host "  [skip] $FileName already exists"
        return
    }
    Write-Host "  [get ] $FileName"
    # Download to a temporary name so an interrupted download is never mistaken
    # for a complete file.
    Invoke-WebRequest -Uri $Url -OutFile "$dest.part" -UseBasicParsing
    Move-Item -Force "$dest.part" $dest
}

Write-Host "Downloading models into $ModelsDir"
Get-Model $SileroUrl "silero_vad.onnx"
foreach ($name in $Whisper) {
    Get-Model "$WhisperBaseUrl/ggml-$name.bin" "ggml-$name.bin"
}
if ($Grammar) {
    Get-Model $GrammarUrl "qwen2.5-0.5b-instruct-q4_k_m.gguf"
}
Write-Host "Done."

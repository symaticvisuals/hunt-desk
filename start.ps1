# One command for a fresh clone on Windows: install what is missing, copy the starting files, open the desk.
# Safe to run again. Each step says what it did. -NoTex skips the TeX engine, -NoDesk skips opening the desk.
# Run it through start.cmd, which gets past PowerShell's script policy.
param([switch]$NoTex, [switch]$NoDesk)
$ErrorActionPreference = 'Stop'
# The progress bar makes Invoke-WebRequest many times slower in Windows PowerShell 5.1.
$ProgressPreference = 'SilentlyContinue'
Set-Location $PSScriptRoot

function Step([string]$Text) {
    Write-Host ''
    Write-Host "· $Text"
}

function Fail([string]$Where, [string]$Again) {
    [Console]::Error.WriteLine('')
    [Console]::Error.WriteLine("Stopped at: $Where")
    [Console]::Error.WriteLine("Run it again with: $Again")
    exit 1
}

$bin = Join-Path $HOME '.local\bin'
$env:Path = "$bin;$env:Path"
$env:PYTHONUTF8 = '1'
$TectonicVersion = '0.17.0'

Step 'uv'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host 'already installed'
} else {
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Fail 'installing uv' 'irm https://astral.sh/uv/install.ps1 | iex'
    }
}

Step 'Python packages'
uv sync
if ($LASTEXITCODE -ne 0) { Fail 'uv sync' 'uv sync' }

Step 'TeX engine for the PDF resumes'
if ($NoTex) {
    Write-Host 'skipped (-NoTex). Resumes are written as .tex until an engine is installed.'
} else {
    uv run python -c "from hunt.pdf import find_tex; raise SystemExit(0 if find_tex() else 1)"
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'already installed'
    } else {
        $url = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40$TectonicVersion/tectonic-$TectonicVersion-x86_64-pc-windows-msvc.zip"
        Write-Host "Installing Tectonic $TectonicVersion into ~\.local\bin (about 30 MB, no admin)."
        $work = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
        try {
            New-Item -ItemType Directory -Force -Path $work, $bin | Out-Null
            $zip = Join-Path $work 'tectonic.zip'
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $work -Force
            $exe = Get-ChildItem -Path $work -Recurse -Filter 'tectonic.exe' | Select-Object -First 1
            Move-Item -Force -Path $exe.FullName -Destination (Join-Path $bin 'tectonic.exe')
        } catch {
            Fail 'installing Tectonic' '.\start.cmd   (or .\start.cmd -NoTex to skip PDF resumes for now)'
        } finally {
            Remove-Item -Recurse -Force -Path $work -ErrorAction SilentlyContinue
        }
    }
}

Step 'Starting files'
uv run python -m hunt seed
if ($LASTEXITCODE -ne 0) { Fail 'copying the starting files' 'uv run python -m hunt seed' }

if (-not $NoTex) {
    Step 'Checking the TeX engine (first run downloads its packages)'
    uv run python -m hunt tex-check
    if ($LASTEXITCODE -eq 0) {
        uv run python -m hunt resume build
    } else {
        Write-Host 'The TeX check failed. Resumes are kept as .tex until it passes: uv run python -m hunt tex-check'
    }
}

if ($NoDesk) {
    Step 'Done. Open the desk with: uv run python -m hunt'
    exit 0
}

Step 'Opening the desk'
uv run python -m hunt
exit $LASTEXITCODE

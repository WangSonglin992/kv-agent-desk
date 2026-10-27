param(
    [string]$IsccPath,
    [switch]$SkipAppBuild
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$distDir = Join-Path $projectDir 'dist'
$exePath = Join-Path $distDir 'KV-Agent-Desk.exe'
$issPath = Join-Path $projectDir 'installer\KV-Agent-Desk.iss'

if ($SkipAppBuild -and $env:GITHUB_ACTIONS -ne 'true') {
    throw 'SkipAppBuild is reserved for CI after a clean executable build. Run this script without SkipAppBuild to rebuild from the current source.'
}

if (-not $SkipAppBuild) {
    & (Join-Path $projectDir 'Build-Dashboard.ps1')
}

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "The current executable is missing: $exePath"
}
if (-not (Test-Path -LiteralPath $issPath -PathType Leaf)) {
    throw "The installer definition is missing: $issPath"
}
if (-not [string]::IsNullOrWhiteSpace($IsccPath)) {
    if ($env:GITHUB_ACTIONS -ne 'true' -or [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        throw 'IsccPath is accepted only from the CI step that verifies the pinned compiler. Omit it locally to download and verify Inno Setup.'
    }
    $verifiedCompiler = Join-Path $env:RUNNER_TEMP 'kv-agent-desk-inno-7.1.0\compiler\ISCC.exe'
    if ([IO.Path]::GetFullPath($IsccPath) -ne [IO.Path]::GetFullPath($verifiedCompiler)) {
        throw 'IsccPath must point to the compiler installed from the verified Inno Setup release in RUNNER_TEMP.'
    }
}

if ($SkipAppBuild) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonPath = $pythonCommand.Source
} else {
    $pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python 3.12 is required to stage the release files: $pythonPath"
}

Push-Location -LiteralPath $projectDir
try {
    $version = (& $pythonPath -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw 'pyproject.toml must contain a stable X.Y.Z project version'
    }
    $packageVersion = (& $pythonPath -c "from agent_dashboard import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $packageVersion -ne $version) {
        throw "Package version $packageVersion does not match pyproject.toml version $version"
    }

    if ([string]::IsNullOrWhiteSpace($IsccPath)) {
        $toolBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } elseif ($env:TEMP) { $env:TEMP } else { [IO.Path]::GetTempPath() }
        $toolRoot = Join-Path $toolBase ('kv-agent-desk-inno-7.1.0-' + [guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $toolRoot
        $assetName = 'innosetup-7.1.0-x64.exe'
        $assetUrl = 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe'
        $installerPath = Join-Path $toolRoot $assetName

        $ghCommand = Get-Command gh -ErrorAction SilentlyContinue
        if (-not $ghCommand) {
            throw 'GitHub CLI is required to verify the official Inno Setup 7.1.0 release attestation before installation.'
        }
        Invoke-WebRequest -Uri $assetUrl -OutFile $installerPath
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw 'The pinned Inno Setup installer download is missing.'
        }
        & $ghCommand.Source release verify-asset is-7_1_0 $installerPath --repo jrsoftware/issrc
        if ($LASTEXITCODE -ne 0) {
            throw 'GitHub release attestation verification failed; the downloaded compiler was not executed.'
        }

        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Pyrsys B\.V\.') {
            throw 'The official Inno Setup Authenticode signature is not valid; the downloaded compiler was not executed.'
        }

        $compilerRoot = Join-Path $toolRoot 'compiler'
        $arguments = @(
            '/VERYSILENT',
            '/SUPPRESSMSGBOXES',
            '/NORESTART',
            '/SP-',
            '/CURRENTUSER',
            ('/DIR="{0}"' -f $compilerRoot)
        )
        $installProcess = Start-Process -FilePath $installerPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
        if ($installProcess.ExitCode -ne 0) {
            throw "Verified Inno Setup installer failed with exit code $($installProcess.ExitCode)"
        }
        $IsccPath = Join-Path $compilerRoot 'ISCC.exe'
    }

    if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
        throw "The Inno Setup 7.1.0 command-line compiler was not found: $IsccPath"
    }
    $compilerVersion = (& $IsccPath --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $compilerVersion -notmatch '(?<![0-9])7\.1\.0(?![0-9])') {
        throw "Expected Inno Setup 7.1.0 x64 compiler; got: $compilerVersion"
    }

    $payloadBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
    $payloadPath = Join-Path $payloadBase ('kv-agent-desk-setup-payload-' + [guid]::NewGuid().ToString('N'))
    & $pythonPath tools/stage_installer_payload.py --exe $exePath --output-dir $payloadPath --project-root $projectDir
    if ($LASTEXITCODE -ne 0) { throw 'Setup payload staging failed' }

    $previousPayload = $env:KV_SETUP_PAYLOAD
    try {
        $env:KV_SETUP_PAYLOAD = $payloadPath
        $compilerArgs = @(
            "--define=AppVersion=$version",
            "--output-dir=$distDir",
            '--output-filename=KV-Agent-Desk-Setup',
            $issPath
        )
        & $IsccPath @compilerArgs
        if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }
    } finally {
        $env:KV_SETUP_PAYLOAD = $previousPayload
    }

    $setupPath = Join-Path $distDir 'KV-Agent-Desk-Setup.exe'
    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
        throw "Inno Setup did not create $setupPath"
    }
    Write-Output $setupPath
} finally {
    Pop-Location
}

# Rebuilds everything, runs the privacy tests, then publishes ONLY the public site (site/) as a single
# fresh commit on the gh-pages branch. The branch is force-pushed, so no earlier version of the site
# (and nothing you later cut out) stays in its history. Run after any change you want online.
param(
    [string]$Remote = 'git@github.com:avkitex/new_england_biking.git',
    [string]$Branch = 'gh-pages'
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the virtual environment first: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt' }

Push-Location $PSScriptRoot
try {
    & $python etl.py
    if ($LASTEXITCODE -ne 0) { throw 'ETL failed; nothing was published.' }
    & $python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed (including the privacy checks); nothing was published.' }
    if (-not (Test-Path 'site\index.html') -or -not (Test-Path 'site\data.json')) { throw 'site/ is incomplete; nothing was published.' }

    # Commit under the GitHub no-reply address, never a personal e-mail.
    $login = (& gh api user --jq .login).Trim()
    $id = (& gh api user --jq .id).Trim()
    if (-not $login -or -not $id) { throw 'Cannot read the GitHub account; run gh auth login.' }
    $email = "$id+$login@users.noreply.github.com"

    $temp = Join-Path ([IO.Path]::GetTempPath()) ('nebiking-pages-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        Copy-Item -Path 'site\*' -Destination $temp -Recurse -Force
        Copy-Item -Path 'site\.nojekyll' -Destination $temp -Force
        git -C $temp init -q -b $Branch
        git -C $temp add -A
        git -C $temp -c "user.name=$login" -c "user.email=$email" commit -q -m 'Publish site'
        git -C $temp push --force $Remote "${Branch}:${Branch}"
        if ($LASTEXITCODE -ne 0) { throw 'Push failed.' }
    }
    finally { Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue }
    Write-Host "Published to $Remote ($Branch)."
}
finally { Pop-Location }

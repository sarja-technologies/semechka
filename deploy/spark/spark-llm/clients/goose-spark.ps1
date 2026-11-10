# Windows (PowerShell): run the Goose CLI against the DGX Spark.
#   1. Install Goose (CLI: goose-x86_64-pc-windows-msvc.zip, or the desktop Goose-win32-x64-setup.exe) from https://github.com/aaif-goose/goose/releases
#   2. Copy spark.json to  $env:APPDATA\Block\goose\config\custom_providers\spark.json   (same file the Linux CLI uses)
#   3. Store the key once, as a user environment variable (the desktop app reads it too after you sign out/in):
#        [Environment]::SetEnvironmentVariable('SPARK_LLM_API_KEY', '<KEY FROM THE SPARK OWNER>', 'User')
#   4. In the desktop app choose provider "DGX Spark (llama.cpp)"; for the CLI:  .\goose-spark.ps1 session
$env:GOOSE_PROVIDER = if ($env:GOOSE_PROVIDER) { $env:GOOSE_PROVIDER } else { 'spark' }
$env:GOOSE_MODEL    = if ($env:GOOSE_MODEL)    { $env:GOOSE_MODEL }    else { 'qwen3-coder-next' }
& goose @args

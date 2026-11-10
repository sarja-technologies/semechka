# Goose on the DGX Spark — client kit

Goose is the upstream open-source agent (https://github.com/aaif-goose/goose, v1.58.1 tested). It talks to the Spark's single
OpenAI-compatible inference API: the llama.cpp router at `http://<spark-lan-ip>:8080/v1` (LAN/VPN only, Bearer key).

Verified coding models (real coding task + MCP tool call + test run, 2026-11-10): `qwen3-coder-next`, `qwen3.8-27b`,
`qwen3.6-35b-a3b`, `gpt-oss-120b`. Others are chat-only until verified (see `~/spark-llm/models.verified.json` on the Spark).
A cold model takes 25-90 s to load on first use; the router keeps up to 3 loaded.

* Linux CLI: download `goose-x86_64-unknown-linux-gnu.tar.gz` (or `aarch64`), verify its sha256 against the GitHub release asset digest,
  put `goose` on PATH, then follow the header of `goose-spark.sh`.
* Linux desktop: `goose_<ver>_amd64.deb` (needs sudo) — it shares `~/.config/goose` with the CLI, so the same `spark.json` appears as a provider.
* Windows: see `goose-spark.ps1`. Config dir `%APPDATA%\Block\goose\config\` (documented by Goose).
* Permissions: `GOOSE_MODE=approve` asks before each tool call; `auto` does not. MCP servers: `--with-extension "<command>"` or `goose configure`.
* The key is a shared secret for the router: give it only to people who may use the Spark; never paste it into chats or commit it.

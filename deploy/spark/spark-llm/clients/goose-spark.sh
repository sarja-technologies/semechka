#!/usr/bin/env bash
# Linux/macOS: run Goose against the DGX Spark. The key is read from a 0600 file (never typed on the command line).
#   install:  mkdir -p ~/.config/goose/custom_providers && cp spark.json ~/.config/goose/custom_providers/
#             printf '%s' '<KEY FROM THE SPARK OWNER>' > ~/.config/goose/spark-llm.key && chmod 600 ~/.config/goose/spark-llm.key
#   use:      goose-spark session        |   goose-spark run -t "task"   |   GOOSE_MODEL=qwen3.6-35b-a3b goose-spark session
export SPARK_LLM_API_KEY="$(cat "$HOME/.config/goose/spark-llm.key")"
export GOOSE_PROVIDER="${GOOSE_PROVIDER:-spark}"
export GOOSE_MODEL="${GOOSE_MODEL:-qwen3-coder-next}"
exec goose "$@"

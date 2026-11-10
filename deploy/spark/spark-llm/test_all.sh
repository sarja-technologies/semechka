#!/usr/bin/env bash
for m in qwen3-coder-next qwen3.6-35b-a3b gpt-oss-120b qwen3-4b-instruct-2507 qwen3.5-0.8b hermes-4-70b dolphin3.0-r1-mistral-24b huihui-qwen3.8-27b-abliterated huihui-qwen3.5-27b-abliterated qwen3.8-27b-uncensored-hauhaucs qwen3.8-flash-next huihui-qwen3.8-flash-next-abliterated; do
  echo "######## $m"; timeout 1500 python3 ~/spark-llm/llmtest.py $m 2>&1 | cut -c1-300 | grep -v "^answer"; echo "exit=$?"
done
echo ALLDONE

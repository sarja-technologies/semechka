# Work Mode (Goose) session image: the Work image plus the UNMODIFIED upstream Goose release binary and a loopback-to-socket relay.
# Build with scripts/spark/goose_image.sh (it supplies ./goose, the aarch64/x86_64 release binary, sha256-checked).
ARG BASE=semechka-work:1
FROM ${BASE}
USER root
COPY --chmod=0755 goose /usr/local/bin/goose
COPY --chmod=0644 llm_forward.py /opt/semechka/llm_forward.py
COPY --chmod=0755 mcp/ /opt/semechka/mcp/
COPY --chmod=0644 ckpt.py /opt/semechka/ckpt.py
RUN chmod 0755 /opt/semechka /opt/semechka/mcp && chmod 0644 /opt/semechka/*.py && chmod 0755 /opt/semechka/mcp/*.py
LABEL org.semechka.goose.image="1"
USER 65534:65534
WORKDIR /work
ENV HOME=/work/.home GOOSE_PATH_ROOT=/work/.home/goose GOOSE_DISABLE_KEYRING=1 GOOSE_TELEMETRY_ENABLED=false
CMD ["python3", "-I", "/opt/semechka/llm_forward.py"]

# Work Mode session image (docs/WORK_MODE.md section 3). One container per session, run by work_runtime.DockerRuntime with
#   --network none --read-only --tmpfs /tmp --user 65534 --cap-drop ALL --security-opt no-new-privileges --init
# The image is built once by scripts/spark/work_image.sh (`docker build --network host`, because the unprivileged daemon
# cannot give a build container a network of its own) and is never pulled at run time (--pull never).
# Everything an agent may need is installed HERE: the running container has no network and a read-only root.
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 MPLBACKEND=Agg
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git nodejs npm ffmpeg ripgrep jq unzip zip ca-certificates build-essential imagemagick \
      poppler-utils fonts-dejavu-core procps file less \
 && rm -rf /var/lib/apt/lists/* \
 # ImageMagick ships a policy that blocks several formats; keep it, it is a sensible default for untrusted input.
 && git config --system safe.directory '*' \
 && git config --system init.defaultBranch main \
 && git config --system user.name "Semechka Work" \
 && git config --system user.email "work@semechka.invalid" \
 && git config --system core.hooksPath /dev/null \
 && git config --system protocol.ext.allow never \
 && git config --system protocol.file.allow user
RUN pip install --no-cache-dir numpy pandas pillow matplotlib requests pytest pyyaml openpyxl python-docx opencv-python-headless
# /work is the persistent workspace (a named volume). Docker copies this directory and its ownership into a new volume.
RUN mkdir -p /work && chown 65534:65534 /work && chmod 0755 /work
LABEL org.semechka.work.image="1"
USER 65534:65534
WORKDIR /work
ENV HOME=/work/.home
CMD ["sleep", "infinity"]

# Execution sandbox image for Semechka agents. Built on the host with `docker build --network host` (the daemon itself
# cannot give containers a network at all); run only with --network none --read-only --cap-drop ALL (see sandbox_svc.py).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/mpl HOME=/work LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg poppler-utils fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pandas numpy scipy openpyxl matplotlib python-docx python-pptx pypdf
COPY runner.py /opt/semechka/runner.py
RUN chmod 0444 /opt/semechka/runner.py && mkdir -p /work && chmod 0755 /work
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python", "-I", "-B", "/opt/semechka/runner.py"]

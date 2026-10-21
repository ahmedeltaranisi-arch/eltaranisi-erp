# Eltaranisi ERP — production-style image (no external Python dependencies)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    ERP_DB=/app/data/erp.db
WORKDIR /app
RUN useradd --create-home --uid 10001 erp
COPY --chown=erp:erp . /app
USER erp
RUN mkdir -p /app/data /app/data/uploads
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status==200 else 1)"
CMD ["python3", "server.py"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# 先裝相依（改程式不用重裝）：照 requirements.txt 的確切版本
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN pip install --no-deps . \
 && useradd --system --uid 10001 app && chown -R app /srv
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

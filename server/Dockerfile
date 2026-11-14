FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# 先裝相依（改程式不用重裝）
COPY pyproject.toml ./
RUN mkdir -p app db graph services settings && touch app/__init__.py db/__init__.py graph/__init__.py services/__init__.py settings/__init__.py \
 && pip install . && pip uninstall -y rag-server

COPY . .
RUN pip install --no-deps . \
 && useradd --system --uid 10001 app && chown -R app /srv
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

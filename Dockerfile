FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# Python 版伺服器的映像檔（Apps Script 版用不到這個檔案）。放在 repo 根目錄：
#   - Railway 等平台會自動找到它，不用另外設定路徑
#   - 建置目錄是 repo 根目錄，才能把 web/ 的聊天頁與後台一起打包
#   docker build -t rag-server .
# 先裝相依（改程式不用重裝）：照 requirements.txt 的確切版本
COPY server/requirements.txt ./
RUN pip install -r requirements.txt

COPY server/ ./
# 聊天頁與後台：伺服器在 /chat/、/admin/ 提供（app/web.py）。只讀不改，GitHub Pages 的前台照舊
COPY web/ ./web/
RUN pip install --no-deps . \
 && useradd --system --uid 10001 app && chown -R app /srv
USER app

# 埠：預設 8000；雲端平台（Railway、Render、Cloud Run…）用環境變數 PORT 指定時照它的
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000'), timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]

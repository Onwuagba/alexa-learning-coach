FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1"
COPY learning_coach ./learning_coach
RUN useradd -m app && mkdir /data && chown app /data
USER app
ENV PROGRESS_DB=/data/progress.db
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen('http://localhost:'+os.environ['PORT']+'/healthz')"
CMD ["python", "-m", "learning_coach.server"]

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
# boto3: Amazon Bedrock Converse + Secrets Manager (used only when enabled by env).
# Build with --build-arg WITH_STRANDS=1 to include the optional Strands Agents tutor (TUTOR_MODE=strands).
ARG WITH_STRANDS=0
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1" boto3 \
 && if [ "$WITH_STRANDS" = "1" ]; then pip install --no-cache-dir "strands-agents>=1.57,<2" "mcp>=1.27,<2"; fi
COPY learning_coach ./learning_coach
RUN useradd -m app && mkdir /data && chown app /data
USER app
ENV PROGRESS_DB=/data/progress.db
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen('http://localhost:'+os.environ['PORT']+'/healthz')"
CMD ["python", "-m", "learning_coach.server"]

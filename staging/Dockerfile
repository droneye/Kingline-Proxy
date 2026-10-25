FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY bot /app/bot
COPY site/backend /app/site/backend
COPY site/dist /app/site/dist
COPY docs /app/docs
RUN chmod -R a+rX /app
USER 10001:10001
WORKDIR /app/bot

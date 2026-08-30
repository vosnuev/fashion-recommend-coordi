FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

EXPOSE 8000

# migrate/collectstatic은 compose command에서 수행한다.
# gunicorn 옵션은 gunicorn.conf.py(WORKDIR /app)에서 읽는다.
CMD ["gunicorn", "config.wsgi:application"]

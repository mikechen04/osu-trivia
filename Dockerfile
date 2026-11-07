FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Build-time only: lets collectstatic run without real secrets.
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home app && chown -R app /app
USER app

ENV PORT=8080
CMD ["./entrypoint.sh"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/service

WORKDIR /service

COPY requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system notifygroup && adduser --system --ingroup notifygroup notifyuser

COPY --chown=notifyuser:notifygroup . /service/alert_notifier/

USER notifyuser

CMD ["python", "-m", "alert_notifier.consumer"]

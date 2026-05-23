web: gunicorn bot:app --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -
pulse_worker: python pulse_worker.py
alert_worker: python alert_worker.py
telegram_worker: python telegram_worker.py
media_worker: python media_worker.py

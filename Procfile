web: sh -c 'gunicorn bot:app --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8} --timeout 120 --access-logfile - --error-logfile -'
undx_worker: python undx_worker.py
email_worker: python email_worker.py
ads_worker: python pulse_ads_worker.py
alert_worker: python alert_worker.py
media_worker: python media_worker.py

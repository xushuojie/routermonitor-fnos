FROM python:3.11-alpine

WORKDIR /app
COPY api.py traffic_history.py /app/

EXPOSE 18199
ENTRYPOINT ["python3", "/app/api.py"]
CMD ["--port", "18199"]

FROM python:3.11-alpine

WORKDIR /app
COPY api.py /app/api.py

EXPOSE 18199
ENTRYPOINT ["python3", "/app/api.py"]
CMD ["--port", "18199"]

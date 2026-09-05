FROM python:3.11-alpine

WORKDIR /app
COPY api.py traffic_history.py network_sources.py web.py storage_discovery.py /app/
COPY web /app/web

RUN mkdir /discovery && chown 65534:65534 /discovery

EXPOSE 18199
ENTRYPOINT ["python3", "/app/api.py"]
CMD ["--port", "18199"]

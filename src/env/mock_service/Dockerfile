FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic

COPY src/env/mock_service /app/src/env/mock_service
COPY src/env/__init__.py /app/src/env/__init__.py
RUN mkdir -p /app/src && touch /app/src/__init__.py

ENV SERVICE_NAME=service_a
ENV SERVICE_ROLE=compute

CMD ["uvicorn", "src.env.mock_service.app:app", "--host", "0.0.0.0", "--port", "8000"]

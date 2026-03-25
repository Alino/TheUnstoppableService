FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY seeds.txt /app/seeds.txt
COPY treasury_state.json /app/treasury_state.json

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "-m", "unstoppable.main", "run-phase2", "--host", "0.0.0.0", "--port", "8080"]

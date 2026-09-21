FROM python:3.12-slim
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY pyproject.toml README.md LICENSE ./
COPY evidenceforge ./evidenceforge
COPY evals ./evals
RUN pip install --no-cache-dir --no-deps . && useradd --create-home app && mkdir /app/data && chown app:app /app/data
USER app
ENV EF_DATA_DIR=/app/data
EXPOSE 8000
CMD ["evidenceforge", "serve", "--host", "0.0.0.0", "--port", "8000"]

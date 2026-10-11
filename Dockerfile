FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY limo_ads_mcp ./limo_ads_mcp
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "limo_ads_mcp.server"]

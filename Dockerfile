# lighter image = faster build
FROM python:3.11-slim

# here code will be copied
WORKDIR /app

# rm for smaller image
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# copy the rest of the code
COPY . .

# default command to run FASTAPI server
CMD ["uvicorn", "app.fast_api_main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
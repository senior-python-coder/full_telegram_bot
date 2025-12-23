FROM python:3.11-slim

# ffmpeg va kerakli paketlarni o‘rnatamiz
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# ishchi papka
WORKDIR /app

# requirements.txt ni o‘rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# kodni qo‘shamiz
COPY . .

CMD ["python", "main.py"]

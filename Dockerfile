# NEEDS UPDATES: 07/24
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \ 
    texlive-latex-base \ 
    texlive-latex-recommended \
    texlive-latex-fonts \
    texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 

COPY . .
CMD ["python", "src/main.py"]

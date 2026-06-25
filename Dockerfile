# OGBot — panel web multicuenta en Docker (expone el puerto 5000)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    OGBOT_CHROMIUM_NO_SANDBOX=1

WORKDIR /app

# Dependencias de Python + Chromium de Playwright (con sus librerías de sistema)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# Código del proyecto
COPY . .

# Panel web (GUI)
EXPOSE 5000

# Las cuentas (config + estado + sesión) persisten aquí; monta un volumen
VOLUME ["/app/accounts"]

# Arranca el panel multicuenta; cada cuenta se inicia/para desde la GUI
CMD ["python", "gui.py"]

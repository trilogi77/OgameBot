# OGBot en Docker

Levanta el **panel web multicuenta** en `http://localhost:5000`. Cada cuenta
corre su propio bot (Chromium headless) dentro del contenedor.

## Arranque rápido (docker compose)

```bash
docker compose up -d --build
```

Abre <http://localhost:5000>, ve a la pestaña **Cuentas**, crea una cuenta y
pon su usuario / contraseña / servidor en **Globales**. Luego pulsa **Iniciar**.

Los datos de las cuentas (config, estado, sesión) persisten en `./accounts`
(montado como volumen), así que sobreviven a reinicios del contenedor.

## Sin docker compose

```bash
docker build -t ogbot .
docker run -d --name ogbot -p 5000:5000 -v "$(pwd)/accounts:/app/accounts" ogbot
```

## Verificación humana (CAPTCHA) en headless

El navegador corre **headless** (sin ventana), por lo que GameForge pide el
"soy humano" más a menudo. No necesitas pantalla: cuando aparezca, el bot
**espera** (hasta `login_human_check_timeout_s`, por defecto 5 min) y te avisa
en el log (y por Telegram si lo configuraste). Resuélvelo así:

1. Abre el panel → pestaña **"Bot en Directo"** (con la cuenta seleccionada).
2. Verás la pantalla real del navegador del bot.
3. Haz clic / escribe para resolver el CAPTCHA.
4. El bot detecta que se resolvió y continúa el login solo.

> El visor en vivo funciona aunque el navegador sea headless: usa el puerto de
> depuración (CDP) interno del contenedor, no hace falta exponerlo.

## Notas

- Para **menos CAPTCHAs**, puedes correr el navegador "headful" con un display
  virtual (Xvfb): cambia el `CMD` a `xvfb-run python gui.py` y pon
  `headless: false` en la cuenta.
- Cada cuenta usa un puerto CDP interno distinto (9222, 9223, …); son internos
  al contenedor.
- `OGBOT_CHROMIUM_NO_SANDBOX=1` (ya puesto en la imagen) añade `--no-sandbox`
  y `--disable-dev-shm-usage`, necesarios para Chromium dentro de Docker.

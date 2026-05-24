# web-to-kindle

Dos herramientas en Python para "consumir" X (Twitter) de forma productiva:

- **`web_to_kindle.py`** — Abre una URL en Chrome (con sesión persistente), hace scroll capturando screenshots del viewport, los combina en un PDF en blanco y negro y lo envía al Kindle por email.
- **`x_filter.py`** — Recorre el feed de X, clasifica cada tweet con un LLM (Cerebras) y aplica acciones: "No me interesa este post" para tweets triviales / improductivos, "Seguir a @usuario" para tweets sobre ciencia, IA, programación, matemáticas y temas técnicos. Las acciones se registran en `x_filter_log.txt`.

## Requisitos

- Python 3.10+
- Google Chrome instalado (para codecs H.264/AAC; si no, hace fallback a Chromium bundled)
- Cuenta de Amazon con email `@kindle.com` configurado (solo para `web_to_kindle.py`)
- Cuenta de Cerebras con API key (solo para `x_filter.py`)

## Instalación

```bash
pip install -r requirements.txt
playwright install chromium
```

Copia `.env.example` a `.env` y rellena los valores:

```bash
cp .env.example .env
```

## Uso

### `web_to_kindle.py` — Web → PDF → Kindle

Edita estas constantes al inicio del archivo:

```python
URL = "https://x.com/home?lang=es"   # URL a capturar
MAX_SCROLLS = 3                       # nº máximo de scrolls
VIEWPORT_WIDTH = 450                  # ancho del viewport en px
VIEWPORT_HEIGHT = 700                 # alto del viewport en px
SCROLL_OVERLAP_PX = 40                # solape entre capturas (evita cortar líneas)
TEXT_SCALE = 0.85                     # escala del texto (1.0 = original)
```

Ejecuta:

```bash
python web_to_kindle.py
```

**Primera ejecución:** abre una pestaña con el formulario de login, te logueas manualmente, pulsas ENTER en la consola y la sesión queda guardada en `browser_profile_chrome/` para futuras ejecuciones.

El PDF se guarda como `<dominio>_<timestamp>.pdf` y se envía automáticamente. Amazon tarda unos minutos en entregarlo al Kindle.

**El PDF es ad-free**: antes de cada captura el script elimina del DOM todos los `<article>` que contengan un `<span>` con el texto `"Anuncio"`, así que ni los anuncios promocionados ni los "tweets sugeridos" como publicidad aparecen en el resultado final.

**El PDF es buscable y los tweets son clickables**: aunque visualmente cada página es una captura de pantalla, el script extrae el texto de cada `<article>` desde el DOM y lo coloca en sus coordenadas como una **capa de texto invisible** (render mode 3). Esto te permite:

- **Seleccionar y copiar** el texto de cualquier tweet con el ratón.
- **Buscar (Ctrl+F)** dentro del PDF.
- **Hacer clic** sobre un tweet para abrirlo en el navegador, o **clic derecho → "Copiar enlace"** para guardarlo/compartirlo. Cada tweet lleva una anotación URI hacia `https://x.com/<usuario>/status/<id>`.

En Kindle esto se traduce en: tocar un tweet → te ofrece abrir el enlace en el navegador experimental del Kindle.

#### Cómo funciona Send-to-Kindle

Cada Kindle tiene una dirección de correo única `xxxxx@kindle.com`. Si **envías un archivo PDF/EPUB/MOBI como adjunto** a esa dirección **desde un correo previamente aprobado en tu cuenta de Amazon**, Amazon lo procesa y lo entrega a tu Kindle por WiFi en pocos minutos. El script simplemente automatiza ese envío SMTP. **No necesitas API key de Amazon ni un token especial**; solo un correo de envío válido.

#### Configuración paso a paso (una sola vez)

**1) Conseguir la dirección de tu Kindle**

Ve a [amazon.com/myk](https://amazon.com/myk) → pestaña **"Devices"** → clic en tu Kindle → ahí aparece la dirección `xxxxx@kindle.com`. Ese valor va en `KINDLE_EMAIL` del `.env`.

**2) Aprobar el correo remitente en Amazon**

Sin este paso Amazon descarta los adjuntos en silencio.

Ve a [amazon.com/myk](https://amazon.com/myk) → pestaña **"Preferences"** → **"Personal Document Settings"** → **"Approved Personal Document E-mail List"** → añade el correo que vas a usar como remitente (el mismo de `SMTP_USER`).

**3) Conseguir la App Password del correo emisor**

Casi todos los proveedores (Gmail, Outlook, Yahoo) bloquean el login SMTP con tu contraseña normal por seguridad. **No es un "token API", sino una contraseña específica de 16 caracteres** que generas en el panel de tu cuenta de correo.

Para **Gmail**:
1. Activa 2FA en [myaccount.google.com/security](https://myaccount.google.com/security) → "Verificación en 2 pasos".
2. Genera una App Password en [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Te da una cadena tipo `abcd efgh ijkl mnop` (16 caracteres). Esa cadena (con o sin espacios) va en `SMTP_PASS`.

Para otros proveedores la mecánica es la misma. Tabla de hosts:

| Proveedor | `SMTP_HOST` | `SMTP_PORT` | Documentación App Password |
|---|---|---|---|
| Gmail | `smtp.gmail.com` | `587` | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| Outlook / Hotmail | `smtp-mail.outlook.com` | `587` | Cuenta Microsoft → Seguridad → Contraseñas de aplicación |
| Yahoo | `smtp.mail.yahoo.com` | `587` | Cuenta Yahoo → Seguridad → Generar app password |
| iCloud | `smtp.mail.me.com` | `587` | Apple ID → Inicio de sesión y seguridad |
| ProtonMail | requiere ProtonMail Bridge local | `1025` | [proton.me/mail/bridge](https://proton.me/mail/bridge) |

**4) Rellenar el `.env`**

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu_correo@gmail.com
SMTP_PASS=abcdefghijklmnop          # App Password de 16 caracteres, NO tu contraseña normal
KINDLE_EMAIL=tu_usuario@kindle.com
```

#### Coste y límites

- **Gratis** — solo usa el SMTP normal de tu proveedor.
- Gmail permite ~500 envíos/día, más que suficiente para uso personal.
- Amazon no cobra por la entrega siempre que estés conectado a WiFi (vía "Personal Documents"). Por 3G/4G cobraría una pequeña tarifa, pero esto solo aplica a los Kindle más antiguos con 3G.

### `x_filter.py` — Filtrado automático del feed de X

Recorre el home de X, clasifica cada tweet con Cerebras y:

- **NO_INTERES**: hace clic en `Más opciones` → `No me interesa este post`
- **INTERES**: hace clic en `Más opciones` → `Seguir a @usuario`
- **NEUTRO**: no toca el tweet

**Personalizar los temas** — edita estas constantes al inicio del archivo:

```python
INTEREST_TOPICS = (
    "ciencia, motivación personal, inteligencia artificial, programación, "
    "matemáticas, ingeniería o temas técnicos serios"
)
UNINTEREST_TOPICS = (
    "sexo, contenido sexual, chismes, farándula, política partidista, "
    "banalidades, drama personal, religión o contenido improductivo/trivial"
)
```

Los textos se inyectan directamente en el system prompt del clasificador, así que basta con cambiarlos para reorientar el filtro a otros dominios (cocina, deportes, finanzas, etc.).

**Parada anticipada** — el script puede detenerse tras N clasificaciones INTERES:

```python
STOP_AFTER_INTEREST = 30   # 0 = desactivado, corre hasta MAX_SCROLLS
```

Útil para no abusar de la API ni hacer crecer demasiado el grafo de "seguidos" en cada ejecución.

Ejecuta:

```bash
python x_filter.py
```

Las acciones se registran en `x_filter_log.txt` con timestamp, categoría, usuario y texto del tweet. También puedes pulsar Ctrl+C para detener.

**Comparte el perfil con `web_to_kindle.py`** (ambos usan `browser_profile_chrome/`), así no hay que loguearse dos veces.

## Estructura del repositorio

```
.
├── web_to_kindle.py       # captura → PDF → email a Kindle
├── x_filter.py            # filtrado automático del feed
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Notas y advertencias

- **Anti-bot de X durante el login**: a veces X detecta automatización en el flujo de login y rechaza silenciosamente (típicamente vuelves al campo del email tras pulsar "Siguiente" en lugar de avanzar a la contraseña). El script abre automáticamente una pestaña nueva para loguearte, que suele pasar el filtro. **Si aun así te sigue rechazando, abre tú mismo una pestaña adicional con `Ctrl+T` dentro de la ventana del script y ve a `https://x.com/login` ahí**: X trata las pestañas abiertas manualmente como acción humana y deja completar el login. Las cookies quedan guardadas en el mismo perfil, así que próximas ejecuciones no necesitarán login.
- **Codecs**: el Chromium bundled de Playwright no incluye H.264/AAC, por lo que los videos de X no se reproducen. El script usa Chrome real por defecto (`USE_CHROME = True`) para evitar este problema.
- **Virtualización del DOM**: X solo mantiene en memoria los tweets visibles, así que hay que capturar a medida que se hace scroll. Por eso `web_to_kindle.py` no usa `page.pdf()` directamente sino que combina screenshots.
- **Texto escalado, imágenes intactas**: el script inyecta CSS que reduce solo `font-size` para que entren más tweets por página sin distorsionar imágenes/videos.

## Privacidad

`browser_profile_chrome/` contiene cookies de sesión (estás logueado en X dentro de él) y `.env` contiene API keys. **Ambos están en `.gitignore`** — nunca los hagas commit.

## Licencia

MIT

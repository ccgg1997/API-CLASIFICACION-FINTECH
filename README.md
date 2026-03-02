# Sureti API

API REST de evaluacion y clasificacion de leads hipotecarios.

## Objetivo

- Recibir datos del lead.
- Evaluar reglas de identidad, inmueble, CTL, perfil financiero, riesgo y politicas.
- Guardar todo en MongoDB usando telefono como llave (`lead_id`).
- Exponer resultados al dashboard en tiempo real.

## Clasificacion oficial

- `NO_VIABLE`
- `VIABLE`
- `ALTAMENTE_VIABLE`

## Stack

- FastAPI
- Pydantic
- PyMongo
- Uvicorn

## Endpoints principales

- `GET /health`
- `POST /api/v1/leads/upsert`
- `GET /api/v1/leads`
- `POST /api/v1/validar-identidad`
- `POST /api/v1/validar-inmueble`
- `POST /api/v1/validar-ctl`
- `POST /api/v1/evaluar-credito`
- `POST /api/v1/analizar-riesgo`
- `POST /api/v1/evaluar-politicas`

## Persistencia en Mongo

- Documento principal en coleccion `leads`.
- Cada endpoint actualiza `validaciones.<modulo>`.
- Campos comunes siempre actualizados:
  - `lead_id`
  - `contacto.celular`
  - `updated_at`
  - `historial[]`
- Politicas tambien actualiza:
  - `decision_sureti`
  - `estado`
  - `etapa`
  - `prioridad`
  - `puntaje_lead`

## Variables de entorno

Crear `.env` en `api/` con base en `.env.example`:

```env
API_PORT=8000
MONGO_URL=mongodb://usuario:password@host:27017/?tls=false
MONGO_DB_NAME=sureti
MONGO_COLLECTION_LEADS=leads
```

## Ejecutar local

```bash
cd api
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Swagger:

- `http://localhost:8000/docs`

## Docker

```bash
cd api
docker compose up --build
```

## EasyPanel

Configura estas variables en el servicio de la API:

- `API_PORT=8000`
- `MONGO_URL=<internal-connection-url-de-mongo>`
- `MONGO_DB_NAME=sureti`
- `MONGO_COLLECTION_LEADS=leads`

Nota: usa la URL interna de Mongo de EasyPanel para baja latencia entre servicios.

## Ejemplo rapido

### 1) Evaluar politicas

```bash
curl -X POST "http://localhost:8000/api/v1/evaluar-politicas" ^
  -H "Content-Type: application/json" ^
  -d "{\"lead_id\":\"573001112233\",\"celular\":\"573001112233\",\"cedula\":\"10203040\",\"ciudad\":\"Bogota\",\"tipo_inmueble\":\"apartamento\",\"valor_inmueble\":350000000,\"ingresos_mensuales\":12000000,\"egresos_mensuales\":3000000,\"cuotas_vigentes\":500000,\"plazo_meses\":240}"
```

### 2) Consultar leads para dashboard (con refresh de clasificacion)

```bash
curl "http://localhost:8000/api/v1/leads?refresh=1"
```

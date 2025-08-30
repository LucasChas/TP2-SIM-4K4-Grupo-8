# Backend (FastAPI) - Generador Aleatorio

## Requisitos
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecutar
```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```
Endpoint principal: `POST http://localhost:8000/generate`

### Ejemplo de request (JSON)
```json
{
  "distribucion": "exponencial",
  "n": 5,
  "params": { "media": 3.5 }
}
```

### Ejemplo de response
```json
{
  "distribucion": "exponencial",
  "n": 5,
  "params": { "media": 3.5 },
  "format": "fixed4",
  "numbers": ["1.2345", "0.9876", "..."]
}
```

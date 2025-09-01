#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API de generación de números aleatorios (4 decimales) para integrar con un front en React.

Distribuciones soportadas y parámetros:
- uniforme: A y B (a < b), n (cantidad)
- exponencial: media > 0, n
- normal: media (mu), desviacion (sigma > 0), n

Notas:
- La fuente U(0,1) es random.random() (función nativa del lenguaje).
- La salida se devuelve en JSON como strings formateadas a 4 decimales para preservar ceros a la derecha.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional, List, Dict, Iterator, Any
import random
import math

app = FastAPI(title="Generador Aleatorio", version="1.1.0")

# CORS para ambiente local (ajusta origins según necesites)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:3000", "http://127.0.0.1:3000", "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def format_es(x: float) -> str:
    # Hasta 4 decimales, sin ceros a la derecha, con coma; siempre al menos 1 decimal
    s = f"{x:.4f}"          # "1.9000"
    s = s.rstrip('0')       # "1.9" o "1."
    if s.endswith('.'):     # evitar "1."
        s += '0'            # -> "1.0"
    s = s.replace('.', ',') # "1,9"
    return s

Distribucion = Literal["uniforme", "exponencial", "normal"]


class UniformeParams(BaseModel):
    A: float = Field(..., description="Límite inferior")
    B: float = Field(..., description="Límite superior")

    @model_validator(mode="after")
    def check_ab(self):
        if not (self.A < self.B):
            raise ValueError("En uniforme se requiere A < B.")
        return self


class ExponencialParams(BaseModel):
    media: float = Field(..., gt=0, description="Media (> 0)")


class NormalParams(BaseModel):
    media: float = Field(..., description="Media (mu)")
    desviacion: float = Field(..., gt=0, description="Desvío estándar (sigma > 0)")


class GenerateRequest(BaseModel):
    distribucion: Distribucion
    n: int = Field(..., gt=0, le=1_000_000, description="Cantidad de valores a generar (1..1,000,000)")
    seed: Optional[int] = Field(None, description="Semilla opcional para reproducibilidad")
    params: Dict = Field(..., description="Parámetros específicos de la distribución")
    k_intervals: Optional[int] = Field(
        None,
        description="Cantidad de intervalos para el histograma (5, 10, 15, 20 o 25)"
    )


class HistogramBin(BaseModel):
    index: int
    a: float         # límite inferior
    b: float         # límite superior
    label: str       # "[a, b)" o "[a, b]"
    freq: int        # frecuencia absoluta
    rel: float       # frecuencia relativa
    cum: int         # frecuencia acumulada
    cum_rel: float   # frecuencia relativa acumulada


class GenerateResponse(BaseModel):
    distribucion: Distribucion
    n: int
    params: Dict
    format: Literal["fixed4"]
    numbers: List[str]
    histogram: Optional[Dict[str, Any]] = None


# -------- Utilidades de generación usando random.random() como U(0,1) --------
def u01() -> float:
    return random.random()


def gen_uniforme(A: float, B: float, n: int) -> Iterator[float]:
    for _ in range(n):
        yield A + (B - A) * u01()  # A + RND(B - A)


def gen_exponencial(media: float, n: int) -> Iterator[float]:
    lam = 1.0 / media
    inv_lam = 1.0 / lam
    for _ in range(n):
        u = u01()
        while u <= 0.0:
            u = u01()
        yield -inv_lam * math.log(1.0 - u)  # -1/lambda * ln(1 - RND)


def gen_normal(mu: float, sigma: float, n: int) -> Iterator[float]:
    """Aproxima N(mu, sigma) por Convolución con 12 uniformes."""
    i = 0
    while i < n:
        s = 0.0
        for _ in range(12):          # 12 uniformes
            s += u01()
        z = s - 6.0                  # media 0, var 1 (porque 12/12 = 1)
        yield mu + sigma * z
        i += 1


# -------------------------- Histograma / Tabla de frecuencias --------------------------
def build_histogram(values: List[float], k: int) -> Dict[str, Any]:
    """
    Construye un histograma con k intervalos: [a0,a1), [a1,a2), ... [a_{k-1}, a_k]
    (último intervalo cerrado a derecha). Devuelve bins, edges y metadatos.
    """
    if k not in {5, 10, 15, 20, 25}:
        raise HTTPException(status_code=422, detail="k_intervals debe ser 5, 10, 15, 20 o 25.")

    mn = min(values)
    mx = max(values)
    if mn == mx:
        # Ensanchar mínimamente si todos los valores son iguales
        eps = 1e-9
        mn -= eps
        mx += eps

    width = (mx - mn) / k
    edges = [mn + i * width for i in range(k + 1)]
    counts = [0] * k

    # Conteo: [a_i, a_{i+1}) salvo el último [a_{k-1}, a_k]
    for x in values:
        if x == edges[-1]:
            idx = k - 1
        else:
            idx = int((x - mn) / width)
            if idx < 0:
                idx = 0
            elif idx >= k:
                idx = k - 1
        counts[idx] += 1

    total = len(values)
    bins: List[HistogramBin] = []
    cum = 0
    for i in range(k):
        a = edges[i]
        b = edges[i + 1]
        freq = counts[i]
        cum += freq
        rel = freq / total
        cum_rel = cum / total
        # Etiqueta de intervalo: último cerrado a derecha
        if i < k - 1:
            label = f"[{a:.4f}, {b:.4f})"
        else:
            label = f"[{a:.4f}, {b:.4f}]"

        bins.append(HistogramBin(
            index=i + 1,
            a=a, b=b, label=label,
            freq=freq, rel=rel,
            cum=cum, cum_rel=cum_rel
        ))

    return {
        "k": k,
        "min": mn,
        "max": mx,
        "width": width,
        "edges": edges,
        "bins": [b.model_dump() for b in bins]
    }


# ----------------------------------- Endpoints ----------------------------------------
@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    if req.seed is not None:
        random.seed(req.seed)

    dist = req.distribucion
    n = req.n

    # Validar y normalizar parámetros
    if dist == "uniforme":
        try:
            p = UniformeParams(**req.params)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        generator = gen_uniforme(p.A, p.B, n)
        params_publicos = {"A": p.A, "B": p.B}

    elif dist == "exponencial":
        try:
            p = ExponencialParams(**req.params)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        generator = gen_exponencial(p.media, n)
        params_publicos = {"media": p.media}

    elif dist == "normal":
        try:
            p = NormalParams(**req.params)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        generator = gen_normal(p.media, p.desviacion, n)
        params_publicos = {"media": p.media, "desviacion": p.desviacion}

    else:
        raise HTTPException(status_code=400, detail="Distribución no soportada.")

    # 1) Genero la serie en float (sirve para histogramar)
    values = list(generator)

    # 2) Formateo a 4 decimales como strings (para mostrar la serie)
    numeros = [format_es(x) for x in values]

    # 3) (Opcional) Histograma
    histogram = None
    if req.k_intervals is not None:
        histogram = build_histogram(values, req.k_intervals)

    return GenerateResponse(
        distribucion=dist,
        n=n,
        params=params_publicos,
        format="fixed4",
        numbers=numeros,
        histogram=histogram
    )


@app.get("/", tags=["health"])
def root():
    return {"ok": True, "msg": "Generador Aleatorio API. POST /generate"}

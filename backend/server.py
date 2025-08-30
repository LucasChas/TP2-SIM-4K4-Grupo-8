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
from typing import Literal, Optional, List, Dict, Iterator
import random
import math

app = FastAPI(title="Generador Aleatorio", version="1.0.0")

# CORS para ambiente local (ajusta origins según necesites)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174", "http://localhost:3000", "http://127.0.0.1:3000", "*"],
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


class GenerateResponse(BaseModel):
    distribucion: Distribucion
    n: int
    params: Dict
    format: Literal["fixed4"]
    numbers: List[str]


# -------- Utilidades de generación usando random.random() como U(0,1) --------
def u01() -> float:
    return random.random()


def gen_uniforme(A: float, B: float, n: int) -> Iterator[float]: #creo que esta bien.
    for _ in range(n):
        yield A + (B - A) * u01() # A + RND(B - A)


def gen_exponencial(media: float, n: int) -> Iterator[float]: #creo que esta bien.
    lam = 1.0 / media
    inv_lam = 1.0 / lam
    for _ in range(n):
        u = u01()
        while u <= 0.0:
            u = u01()
        yield -inv_lam * math.log(1.0 - u) # -1/lambda * ln(1 - RND)


def gen_normal(mu: float, sigma: float, n: int) -> Iterator[float]: #validar....
    i = 0
    while i < n: #limito a n.
        u1 = u01() #genero los randoms.
        while u1 <= 0.0:
            u1 = u01()
        u2 = u01()
        r = math.sqrt(-2.0 * math.log(u1))
        theta = 2.0 * math.pi * u2
        z1 = r * math.cos(theta)
        z2 = r * math.sin(theta)
        yield mu + sigma * z1
        i += 1
        if i < n:
            yield mu + sigma * z2
            i += 1


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

    # Formatear a 4 decimales como strings
    numeros = [format_es(x) for x in generator]

    return GenerateResponse(
        distribucion=dist,
        n=n,
        params=params_publicos,
        format="fixed4",
        numbers=numeros,
    )


@app.get("/", tags=["health"])
def root():
    return {"ok": True, "msg": "Generador Aleatorio API. POST /generate"}

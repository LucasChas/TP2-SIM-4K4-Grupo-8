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
    """Genera N(mu, sigma) usando el método de Box–Muller (versión trigonométrica)."""
    i = 0
    two_pi = 2.0 * math.pi
    while i < n:
        # Evitar u1=0 para que log(u1) sea válido
        u1 = u01()
        while u1 <= 1e-12:
            u1 = u01()
        u2 = u01()

        r = math.sqrt(-2.0 * math.log(u1))
        theta = two_pi * u2

        z0 = r * math.cos(theta)
        yield mu + sigma * z0
        i += 1

        if i < n:
            z1 = r * math.sin(theta)
            yield mu + sigma * z1
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


# ============================ Goodness of Fit (Chi-Cuadrado) ============================
from pydantic import BaseModel

def cdf_uniform(A: float, B: float, x: float) -> float:
    if x <= A: return 0.0
    if x >= B: return 1.0
    return (x - A) / (B - A)

def cdf_exponencial(media: float, x: float) -> float:
    if x <= 0.0: return 0.0
    lam = 1.0 / media
    return 1.0 - math.exp(-lam * x)

def cdf_normal(mu: float, sigma: float, x: float) -> float:
    t = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(t))

# Inversa normal (Φ⁻¹) – aproximación de Acklam (suficiente para α comunes)
def inv_norm(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p debe estar en (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2*math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if phigh < p:
        q = math.sqrt(-2*math.log(1-p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                 ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    q = p - 0.5
    r = q*q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)

def chi2_crit(df: int, alpha: float) -> float:
    # Wilson–Hilferty approx. para F^{-1}(1-α)
    if df < 1: df = 1
    z = inv_norm(1.0 - alpha)
    return df * (1.0 - 2.0/(9.0*df) + z * math.sqrt(2.0/(9.0*df)))**3

class GoFRequest(BaseModel):
    distribucion: Distribucion
    params: Dict
    n: int
    edges: List[float]
    observed: List[int]
    alpha: float = Field(0.05, gt=0, lt=1)

class GoFRow(BaseModel):
    index: int
    label: str
    p: float
    expected: float
    observed: int
    contrib: float

class GoFResponse(BaseModel):
    H0: str
    H1: str
    alpha: float
    df: int
    chi2_obs: float
    chi2_crit: float
    reject: bool
    warning: Optional[str] = None
    rows: List[GoFRow]

@app.post("/gof", response_model=GoFResponse)
def goodness_of_fit(req: GoFRequest):
    k = len(req.observed)
    if len(req.edges) != k + 1:
        raise HTTPException(status_code=422, detail="edges debe tener k+1 elementos.")
    if k < 2:
        raise HTTPException(status_code=422, detail="Se requieren al menos 2 intervalos.")

    # Probabilidades por intervalo según la CDF
    if req.distribucion == "uniforme":
        A, B = req.params["A"], req.params["B"]
        cdf = lambda x: cdf_uniform(A, B, x)
        p_params = 0   # <-- no estimamos A y B → p = 0
        H0 = f"Los datos ~ Uniforme[{A:.4f}, {B:.4f}] (parámetros fijados)"
        H1 = "Los datos NO siguen esa Uniforme."
    elif req.distribucion == "exponencial":
        media = req.params["media"]
        cdf = lambda x: cdf_exponencial(media, x)
        p_params = 1
        H0 = f"Los datos ~ Exponencial(media={media:.4f})"
        H1 = "Los datos NO siguen esa Exponencial."
    elif req.distribucion == "normal":
        mu, sigma = req.params["media"], req.params["desviacion"]
        cdf = lambda x: cdf_normal(mu, sigma, x)
        p_params = 2
        H0 = f"Los datos ~ Normal(μ={mu:.4f}, σ={sigma:.4f})"
        H1 = "Los datos NO siguen esa Normal."
    else:
        raise HTTPException(status_code=400, detail="Distribución no soportada.")

    probs = []
    rows: List[GoFRow] = []
    warning = None
    for i in range(k):
        a, b = req.edges[i], req.edges[i+1]
        pi = max(0.0, min(1.0, cdf(b) - cdf(a)))
        probs.append(pi)
    # Normalizar pequeñas desviaciones numéricas
    s = sum(probs)
    if s <= 0:
        raise HTTPException(status_code=422, detail="Las probabilidades teóricas resultaron 0.")
    probs = [p/s for p in probs]

    chi2 = 0.0
    any_small = False
    for i, (pi, oi) in enumerate(zip(probs, req.observed)):
        expected = req.n * pi
        contrib = 0.0 if expected <= 0 else ((oi - expected)**2) / expected
        chi2 += contrib
        label = f"[{req.edges[i]:.4f}, {req.edges[i+1]:.4f}" + ("]" if i == k-1 else ")")
        rows.append(GoFRow(index=i+1, label=label, p=pi, expected=expected, observed=oi, contrib=contrib))
        if expected < 5.0:
            any_small = True

    df = max(1, k - 1 - p_params)  # gl = k - 1 - (#parámetros)
    crit = chi2_crit(df, req.alpha)
    reject = chi2 > crit
    if any_small:
        warning = "Hay intervalos con frecuencia esperada < 5. Considerá reagrupar."

    return GoFResponse(H0=H0, H1=H1, alpha=req.alpha, df=df,
                       chi2_obs=chi2, chi2_crit=crit, reject=reject,
                       warning=warning, rows=rows)





@app.get("/", tags=["health"])
def root():
    return {"ok": True, "msg": "Generador Aleatorio API. POST /generate"}

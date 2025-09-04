#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador de números aleatorios (4 decimales) para distribuciones:
- Uniforme [a, b]
- Exponencial (parámetro: media o lambda)
- Normal (parámetros: mu y sigma)

Requisitos del enunciado:
- La fuente de aleatoriedad U(0,1) usa la función nativa del lenguaje (random.random).
- Salida con 4 dígitos decimales.

Ejemplos de uso:
    # 5 uniformes en [10, 20]
    python generador_aleatorio.py uniforme -n 5 --a 10 --b 20

    # 5 exponenciales con media 3.5
    python generador_aleatorio.py exponencial -n 5 --media 3.5

    # 5 exponenciales con lambda 0.4
    python generador_aleatorio.py exponencial -n 5 --lambda 0.4

    # 5 normales N(mu=10, sigma=2)
    python generador_aleatorio.py normal -n 5 --mu 10 --sigma 2

    # Semilla fija para reproducibilidad
    python generador_aleatorio.py uniforme -n 5 --a 0 --b 1 --seed 1234
"""
import argparse
import math
import random
import sys
from typing import Iterable, Iterator, Optional


def u01() -> float:
    """U(0,1) usando la función nativa random.random().
    Devuelve valores en [0.0, 1.0)."""
    return random.random()


def generar_uniforme(a: float, b: float, n: int) -> Iterator[float]:
    """Uniforme continua en [a, b] por transformación: a + (b-a)*U."""
    if not (a < b):
        raise ValueError("Para uniforme, se requiere a < b.")
    for _ in range(n):
        u = u01()
        yield a + (b - a) * u


def generar_exponencial(media: Optional[float], lam: Optional[float], n: int) -> Iterator[float]:
    """Exponencial por inversa: X = -(1/λ) * ln(1-U).

    Parámetros (use EXACTAMENTE uno):
      - media > 0  => λ = 1/media
      - lam   > 0  => λ = lam
    """
    if (media is None) == (lam is None):
        raise ValueError("Para exponencial, indique exactamente uno: --media o --lambda.")
    if media is not None:
        if media <= 0:
            raise ValueError("La media debe ser > 0.")
        lamda = 1.0 / media
    else:
        if lam is None or lam <= 0:
            raise ValueError("Lambda debe ser > 0.")
        lamda = lam

    inv_lam = 1.0 / lamda
    for _ in range(n):
        # Evitar log(0): aseguramos U en (0,1)
        u = u01()
        while u <= 0.0:
            u = u01()
        yield -inv_lam * math.log(1.0 - u)


def generar_normal(mu: float, sigma: float, n: int) -> Iterator[float]:
    """Normal N(mu, sigma^2) usando Box-Muller clásico (transformación polar).

    Z ~ N(0,1): Z = sqrt(-2 ln U1) * cos(2π U2), con U1,U2 ~ U(0,1) independientes.
    """
    if sigma <= 0:
        raise ValueError("Sigma debe ser > 0.")
    i = 0
    while i < n:
        u1 = u01()
        # Evitar log(0)
        while u1 <= 0.0:
            u1 = u01()
        u2 = u01()
        r = math.sqrt(-2.0 * math.log(u1))
        theta = 2.0 * math.pi * u2
        z1 = r * math.cos(theta)
        z2 = r * math.sin(theta)
        x1 = mu + sigma * z1
        yield x1
        i += 1
        if i < n:
            x2 = mu + sigma * z2
            yield x2
            i += 1


def validar_n(n: int) -> int:
    if n <= 0:
        raise argparse.ArgumentTypeError("El número de valores (-n/--n) debe ser un entero > 0.")
    return n


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera números aleatorios con 4 decimales para uniforme, exponencial o normal."
    )
    parser.add_argument(
        "distribucion",
        choices=["uniforme", "exponencial", "normal"],
        help="Tipo de distribución a generar."
    )
    parser.add_argument("-n", "--n", type=int, required=True, help="Cantidad de números a generar (entero > 0).")
    parser.add_argument("--seed", type=int, help="Semilla opcional para reproducibilidad (entero).")

    # Parámetros por distribución
    # Uniforme
    parser.add_argument("--a", type=float, help="Límite inferior (uniforme).")
    parser.add_argument("--b", type=float, help="Límite superior (uniforme).")

    # Exponencial (mutuamente excluyentes)
    parser.add_argument("--media", type=float, help="Media (exponencial). Debe ser > 0.")
    parser.add_argument("--lambda", dest="lam", type=float, help="Lambda (tasa) (exponencial). Debe ser > 0.")

    # Normal
    parser.add_argument("--mu", type=float, help="Media (normal).")
    parser.add_argument("--sigma", type=float, help="Desvío estándar (normal). Debe ser > 0.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    # Validaciones generales
    n = validar_n(args.n)
    if args.seed is not None:
        random.seed(args.seed)

    gen: Iterator[float]

    if args.distribucion == "uniforme":
        if args.a is None or args.b is None:
            parser.error("Uniforme requiere --a y --b.")
        gen = generar_uniforme(args.a, args.b, n)

    elif args.distribucion == "exponencial":
        gen = generar_exponencial(args.media, args.lam, n)

    else:  # normal
        if args.mu is None or args.sigma is None:
            parser.error("Normal requiere --mu y --sigma.")
        gen = generar_normal(args.mu, args.sigma, n)

    # Imprimir con 4 decimales, uno por línea
    for x in gen:
        # Se usa formato fijo a 4 decimales (incluye ceros a la derecha)
        print(f"{x:.4f}")

    return 0

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





if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Permite canalizar la salida (| head) sin errores de pipe
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)

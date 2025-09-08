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

    Parámetros:
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

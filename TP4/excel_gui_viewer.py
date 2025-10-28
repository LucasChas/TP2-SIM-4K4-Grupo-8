import tkinter as tk
from tkinter import ttk, messagebox
import json
import random
import math
from collections import deque
import sqlite3
import tempfile
import os

APP_TITLE = "Parámetros de Simulación - Biblioteca (Tabla virtualizada / RAM estable)"
GROUP_BG = "#e8efff"
GROUP_BORDER = "#a8b3d7"
MAX_CAPACITY = 20  # Máximo total de personas dentro (2 bibliotecarios + hasta 18 clientes)


# ----------------- Utilidades simples -----------------
def int_or_none(s: str):
    try:
        return int(s)
    except Exception:
        return None


def between(value, lo=None, hi=None):
    if value is None:
        return False
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def fmt(x, nd=2):
    if x is None or x == "":
        return ""
    return f"{x:.{nd}f}"


# ----------------- Modelos -----------------
class Cliente:
    def __init__(self, cid, hora_llegada):
        self.id = cid
        # estados posibles: "EN COLA", "SIENDO ATENDIDO(1)", "SIENDO ATENDIDO(2)",
        # "EC LEYENDO", "DESTRUCCION"
        self.estado = "EN COLA"

        # Tiempos clave
        self.hora_llegada = hora_llegada      # float (para permanencia total en el sistema)
        self.hora_entrada_cola = hora_llegada # cuando entra/reingresa a cola

        # Motivo / acción
        self.a_que_fue_inicial = ""   # primera acción declarada ("Pedir", "Devolver", "Consultar")
        self.accion_actual = ""       # acción actual

        # Lectura en biblioteca
        self.fin_lect_num = None      # float fin de lectura
        self.cuando_termina_leer = "" # string amigable del fin de lectura


class Bibliotecario:
    def __init__(self):
        self.estado = "LIBRE"   # "LIBRE" / "OCUPADO"
        self.rnd = ""           # RND del servicio actual
        self.demora = ""        # demora servicio actual
        self.hora = ""          # fin de servicio estimado (string)
        self.hora_num = None    # fin de servicio estimado (float)
        self.cliente_id = None  # ID del cliente al que atiende


# ----------------- Motor de simulación -----------------
class SimulationEngine:
    """
    Motor de eventos discretos. Cada avance puede ser:
      - LLEGADA_CLIENTE
      - FIN_ATENCION_i
      - FIN_LECTURA

    Métricas pedidas:
    - tiempo libre bibliotecario en ESTA iteración = dt entre evento anterior y este,
      sólo si el bibliotecario estuvo LIBRE todo ese dt;
    - acumulador de ocio total de bibliotecarios = suma histórica de sus tiempos libres;
    - acumulador de permanencia de clientes = suma histórica de (reloj_salida - hora_llegada)
      sólo cuando el cliente sale del sistema (DESTRUCCION).
    """

    def __init__(self, cfg):
        self.cfg = cfg

        # Parámetros generales
        self.t_inter = cfg["llegadas"]["tiempo_entre_llegadas_min"]

        self.p_pedir = cfg["motivos"]["pedir_libros_pct"] / 100.0
        self.p_devolver = cfg["motivos"]["devolver_libros_pct"] / 100.0
        self.p_consultar = cfg["motivos"]["consultar_socios_pct"] / 100.0

        self.uni_a = cfg["consultas_uniforme"]["a_min"]
        self.uni_b = cfg["consultas_uniforme"]["b_min"]

        self.p_retira = cfg["lectura"]["retira_casa_pct"] / 100.0
        self.t_lect_biblio = cfg["lectura"]["tiempo_fijo_biblioteca_min"]

        self.time_limit = cfg["simulacion"]["tiempo_limite_min"]
        self.iter_limit = cfg["simulacion"]["iteraciones_max"]

        # Estado temporal
        self.clock = cfg["simulacion"]["mostrar_vector_estado"]["desde_minuto_j"]
        self.last_clock = self.clock
        self.next_arrival = self.clock + self.t_inter

        self.iteration = 0
        self.next_client_id = 1

        # Estado de clientes
        self.cola = deque()            # cola FIFO de IDs de cliente
        self.clientes = {}             # id -> Cliente (activos / recién destruidos)
        self._to_clear_after_emit = set()  # IDs para borrar antes del siguiente evento

        # Bibliotecarios
        self.bib = [Bibliotecario(), Bibliotecario()]

        # Personas leyendo físicamente en sala
        self.biblio_personas_cnt = 0
        self.biblio_estado = ""
        self._update_biblio_estado()

        # Valores mostrados sólo en la fila actual (por bibliotecario)
        self.last_b = {
            1: {"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""},
            2: {"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""},
        }

        # Métricas acumuladas globales
        self.est_b1_libre_acum = 0.0
        self.est_b2_libre_acum = 0.0
        self.est_bib_ocioso_acum = 0.0  # suma histórica total entre ambos

        self.last_iter_b1_libre = 0.0   # dt libre en ESTA iteración (b1)
        self.last_iter_b2_libre = 0.0   # dt libre en ESTA iteración (b2)

        # Permanencia de clientes destruidos
        self.cli_perm_acum_total = 0.0
        self.cli_completados = 0
        self.sum_tiempo_en_sistema = 0.0

        self._finalizado = False

    # ---------- helpers internos ----------
    def _clear_destroyed_clients(self):
        """
        Borra definitivamente los clientes marcados como destruidos en la iteración anterior,
        para que dejen de mostrarse en las columnas Cliente N en las filas siguientes.
        """
        if not self._to_clear_after_emit:
            return
        for cid in list(self._to_clear_after_emit):
            if cid in self.clientes:
                del self.clientes[cid]
        self._to_clear_after_emit.clear()

    def _hay_cola(self):
        return len(self.cola) > 0

    def _primer_bib_libre(self):
        if self.bib[0].estado == "LIBRE":
            return 0
        if self.bib[1].estado == "LIBRE":
            return 1
        return None

    def _elige_transaccion(self, rnd_val):
        """
        rnd_val en [0,1)
        decide si es Pedir / Devolver / Consultar
        """
        if rnd_val < self.p_pedir:
            return "Pedir"
        elif rnd_val < self.p_pedir + self.p_devolver:
            return "Devolver"
        else:
            return "Consultar"

    def _sortear_transaccion_si_falta(self, cliente: Cliente):
        """
        Si el cliente todavía no tiene acción_actual, la sorteamos ahora.
        Devuelve (rnd_trx, tipo_trx) para la fila de esta iteración.
        """
        if cliente.accion_actual:
            return "", cliente.accion_actual

        rnd_trx_val = random.random()
        tipo = self._elige_transaccion(rnd_trx_val)
        cliente.a_que_fue_inicial = tipo
        cliente.accion_actual = tipo
        return fmt(rnd_trx_val, 4), tipo

    def _demora_por_transaccion(self, tipo):
        """
        Según el tipo de transacción, genera rnd_srv y demora:
        - Consultar -> Uniforme(A,B)
        - Devolver -> Uniforme(1.5,2.5)
        - Pedir     -> Exponencial(media=6)
        """
        r = random.random()
        if tipo == "Consultar":
            demora = self.uni_a + (self.uni_b - self.uni_a) * r
        elif tipo == "Devolver":
            demora = 1.5 + r * (2.5 - 1.5)
        else:  # "Pedir"
            demora = -6.0 * math.log(1.0 - r)
        return r, demora

    def _tomar_de_cola(self, idx_bib):
        """
        Si hay cliente en cola y el bibliotecario idx_bib está libre,
        lo atiende inmediatamente.

        Devuelve (hubo_asignacion, rnd_serv, demora, trx_rnd, trx_tipo)
        para mostrar en la fila actual.
        """
        if not self.cola:
            return False, "", "", "", ""

        b = self.bib[idx_bib]
        cid = self.cola.popleft()
        c = self.clientes.get(cid)
        if c is None:
            return False, "", "", "", ""

        trx_rnd, trx_tipo = self._sortear_transaccion_si_falta(c)
        c.estado = f"SA({idx_bib + 1})"

        rnd_srv, demora = self._demora_por_transaccion(c.accion_actual)
        b.estado = "OCUPADO"
        b.rnd = fmt(rnd_srv)
        b.demora = fmt(demora)
        b.hora_num = self.clock + demora
        b.hora = fmt(b.hora_num)
        b.cliente_id = cid

        return True, b.rnd, b.demora, trx_rnd, trx_tipo

    def _current_clients_occupying_spot(self):
        """
        Cuántos clientes están físicamente presentes:
        - en cola
        - siendo atendidos
        - leyendo en la biblioteca
        """
        en_servicio = (1 if self.bib[0].estado == "OCUPADO" else 0) + \
                      (1 if self.bib[1].estado == "OCUPADO" else 0)
        return len(self.cola) + en_servicio + self.biblio_personas_cnt

    def _total_people_present_for_display(self):
        """
        Total físico dentro de la biblioteca:
        2 bibliotecarios + los clientes presentes (cola / atención / leyendo).
        """
        return 2 + self._current_clients_occupying_spot()

    def _update_biblio_estado(self):
        """
        Biblioteca "Abierta" o "Cerrada" según capacidad.
        """
        if self._total_people_present_for_display() >= MAX_CAPACITY:
            self.biblio_estado = "Cerrada"
        else:
            self.biblio_estado = "Abierta"

    def _integrar_estadisticas_hasta(self, new_time: float):
        """
        Integra las estadísticas de ocio de bibliotecarios
        desde self.last_clock hasta new_time.
        """
        dt = new_time - self.last_clock

        # reset por-iteración
        self.last_iter_b1_libre = 0.0
        self.last_iter_b2_libre = 0.0

        if dt <= 0:
            self.last_clock = new_time
            return

        # bibliotecario 1
        if self.bib[0].estado == "LIBRE":
            self.last_iter_b1_libre = dt
            self.est_b1_libre_acum += dt

        # bibliotecario 2
        if self.bib[1].estado == "LIBRE":
            self.last_iter_b2_libre = dt
            self.est_b2_libre_acum += dt

        # acumulador histórico total de ocio
        self.est_bib_ocioso_acum = self.est_b1_libre_acum + self.est_b2_libre_acum

        self.last_clock = new_time

    def _proximo_evento(self):
        """
        Devuelve el próximo evento como tupla (t, prioridad, tipo, data).
        prioridad:
          1 FIN_ATENCION_1
          2 FIN_ATENCION_2
          3 FIN_LECTURA (desempate leve con cid)
          4 LLEGADA_CLIENTE
        """
        cand = []

        # Fines de atención
        if self.bib[0].hora_num is not None:
            cand.append((self.bib[0].hora_num, 1, "fin_atencion", {"i": 1}))
        if self.bib[1].hora_num is not None:
            cand.append((self.bib[1].hora_num, 2, "fin_atencion", {"i": 2}))

        # Fines de lectura
        for cid, c in self.clientes.items():
            if c.estado == "EC LEYENDO" and c.fin_lect_num is not None:
                cand.append((c.fin_lect_num, 3 + cid * 1e-6, "fin_lectura", {"cid": cid}))

        # Próxima llegada
        if self.next_arrival is not None:
            cand.append((self.next_arrival, 4, "llegada", {}))

        if not cand:
            return None

        return min(cand, key=lambda x: (x[0], x[1]))

    def hay_mas(self):
        """
        ¿Queda al menos un evento para disparar dentro de límites?
        """
        self._clear_destroyed_clients()

        ne = self._proximo_evento()
        if ne is None:
            return False
        t, *_ = ne
        return (self.iteration < self.iter_limit) and (t <= self.time_limit)

    # ---------- snapshots para la UI ----------
    def build_client_snapshot(self):
        """
        Snapshot para las columnas dinámicas Cliente N.
        Incluye clientes “DESTRUCCION” en ESTA iteración,
        se limpian recién en la siguiente iteración.
        """
        snap = {}
        for cid, c in self.clientes.items():
            snap[cid] = {
                "estado": c.estado,
                "hora_llegada": fmt(c.hora_llegada, 2),
                "a_que_fue": c.accion_actual or c.a_que_fue_inicial,
                "cuando_termina": c.cuando_termina_leer,
            }
        return snap

    def snapshot_estadisticas(self):
        """
        Datos globales para ventana de estadísticas (promedios, etc.)
        """
        prom_permanencia = (
            self.sum_tiempo_en_sistema / self.cli_completados
            if self.cli_completados > 0
            else 0.0
        )
        return {
            "clientes_completados": self.cli_completados,
            "prom_permanencia": prom_permanencia,
            "b1_ocioso": self.est_b1_libre_acum,
            "b2_ocioso": self.est_b2_libre_acum,
            "total_ocioso": self.est_bib_ocioso_acum,
        }

    def finalizar_estadisticas(self):
        """
        Integra hasta time_limit si faltaba un último tramo.
        """
        if not self._finalizado and self.last_clock < self.time_limit:
            self._integrar_estadisticas_hasta(self.time_limit)
        self._finalizado = True
        return self.snapshot_estadisticas()

    # ---------- EVENTOS PRINCIPALES ----------
    def siguiente_evento(self):
        """
        Avanza 1 evento y devuelve:
         - row_dict con datos base (evento, reloj, etc.)
         - cli_snap para columnas Cliente N
        """
        self._clear_destroyed_clients()

        ne = self._proximo_evento()
        if ne is None:
            raise StopIteration("No hay más eventos pendientes.")
        t, _, tipo, data = ne
        if self.iteration >= self.iter_limit:
            raise StopIteration("Máximo de iteraciones alcanzado.")
        if t > self.time_limit:
            raise StopIteration("Se alcanzó el tiempo límite X.")

        if tipo == "llegada":
            row, snap = self._evento_llegada()
        elif tipo == "fin_atencion":
            row, snap = self._evento_fin_atencion(data["i"])
        else:
            row, snap = self._evento_fin_lectura(data["cid"])

        return row, snap

    def _evento_llegada(self):
        """
        Evento: LLEGADA_CLIENTE
        """
        t = self.next_arrival

        # Integramos ocio hasta este tiempo
        self._integrar_estadisticas_hasta(t)

        # Avanzamos
        self.iteration += 1
        self.clock = t

        # Permanencia sumada en ESTA iteración por clientes que salen YA
        event_perm_sum = 0.0

        # reset columnas por-iteración de bibliotecarios
        self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
        self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

        # Llega nuevo cliente
        cid = self.next_client_id
        self.next_client_id += 1
        c = Cliente(cid, hora_llegada=self.clock)

        trx_rnd = ""
        trx_tipo = ""

        # Chequeo de capacidad
        if self._current_clients_occupying_spot() >= (MAX_CAPACITY - 2):
            # No entra → destruido Forzado
            c.estado = "DESTRUCCION"
            c.fin_lect_num = None
            c.cuando_termina_leer = "CLIENTE DESTRUIDO (CAPACIDAD MAXIMA)"
            self.clientes[cid] = c

            tiempo_perm = (self.clock - c.hora_llegada)
            event_perm_sum += tiempo_perm
            self.sum_tiempo_en_sistema += tiempo_perm
            self.cli_completados += 1

            # Se lo limpia en el próximo evento
            self._to_clear_after_emit.add(c.id)
        else:
            # Puede entrar
            libre = self._primer_bib_libre()
            if (not self._hay_cola()) and (libre is not None):
                # Pasa directo con bibliotecario libre
                trx_rnd, trx_tipo = self._sortear_transaccion_si_falta(c)
                c.estado = f"SA({libre + 1})"

                rnd_srv, demora = self._demora_por_transaccion(c.accion_actual)
                b = self.bib[libre]
                b.estado = "OCUPADO"
                b.rnd = fmt(rnd_srv)
                b.demora = fmt(demora)
                b.hora_num = self.clock + demora
                b.hora = fmt(b.hora_num)
                b.cliente_id = c.id

                # Guardamos para mostrar esta iteración
                self.last_b[libre + 1]["rnd"] = b.rnd
                self.last_b[libre + 1]["demora"] = b.demora
                self.last_b[libre + 1]["trx_rnd"] = trx_rnd
                self.last_b[libre + 1]["trx_tipo"] = trx_tipo
            else:
                # Va a cola
                c.estado = "EN COLA"
                c.hora_entrada_cola = self.clock
                self.cola.append(c.id)

            self.clientes[cid] = c

        # Programar próxima llegada
        self.next_arrival = self.clock + self.t_inter

        # Actualizar estado biblioteca
        self._update_biblio_estado()

        # Acumular permanencia global
        self.cli_perm_acum_total += event_perm_sum

        row = {
            "evento": f"LLEGADA_CLIENTE({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": fmt(self.t_inter, 2),
            "lleg_minuto": fmt(self.next_arrival, 2),
            "lleg_id": str(cid),
            "trx_rnd": trx_rnd,
            "trx_tipo": trx_tipo,
            "lee_rnd": "",
            "lee_lugar": "",
            "lee_tiempo": "",
            "lee_fin": "",
            "b1_estado": self.bib[0].estado,
            "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.last_b[1]["demora"],
            "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado,
            "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.last_b[2]["demora"],
            "b2_hora": self.bib[1].hora,
            "cola": len(self.cola),
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self._total_people_present_for_display(),
            "est_b1_libre": fmt(self.last_iter_b1_libre),
            "est_b2_libre": fmt(self.last_iter_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.cli_perm_acum_total),
        }

        cli_snap = self.build_client_snapshot()
        return row, cli_snap

    def _evento_fin_atencion(self, i):
        """
        FIN_ATENCION_i
        """
        idx = i - 1
        b = self.bib[idx]
        t = b.hora_num

        self._integrar_estadisticas_hasta(t)

        self.iteration += 1
        self.clock = t

        event_perm_sum = 0.0

        # limpiar info por-iteración
        self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
        self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

        cid = b.cliente_id
        c = self.clientes[cid]

        lee_rnd = ""
        lee_lugar = ""
        lee_tiempo = ""
        lee_fin = ""

        if c.accion_actual == "Pedir":
            # decide lectura en casa vs en biblioteca
            r = random.random()
            lee_rnd = fmt(r, 4)
            if r < self.p_retira:
                # se va con el libro -> destrucción inmediata
                c.estado = "DESTRUCCION"
                c.fin_lect_num = None
                c.cuando_termina_leer = ""
                tiempo_perm = (self.clock - c.hora_llegada)
                event_perm_sum += tiempo_perm
                self.sum_tiempo_en_sistema += tiempo_perm
                self.cli_completados += 1
                self._to_clear_after_emit.add(c.id)
            else:
                # se queda a leer en biblioteca
                c.estado = "EC LEYENDO"
                fin_lec = self.clock + self.t_lect_biblio
                c.fin_lect_num = fin_lec
                c.cuando_termina_leer = fmt(fin_lec, 2)
                lee_lugar = "Biblioteca"
                lee_tiempo = fmt(self.t_lect_biblio, 2)
                lee_fin = c.cuando_termina_leer
                self.biblio_personas_cnt += 1
        else:
            # Devolver / Consultar => sale del sistema
            c.estado = "DESTRUCCION"
            c.fin_lect_num = None
            c.cuando_termina_leer = ""
            tiempo_perm = (self.clock - c.hora_llegada)
            event_perm_sum += tiempo_perm
            self.sum_tiempo_en_sistema += tiempo_perm
            self.cli_completados += 1
            self._to_clear_after_emit.add(c.id)

        # bibliotecario queda libre
        b.estado = "LIBRE"
        b.rnd = ""
        b.demora = ""
        b.hora = ""
        b.hora_num = None
        b.cliente_id = None

        # intenta agarrar siguiente en cola
        asigno, rnd_b, demora_b, trx_rnd, trx_tipo = self._tomar_de_cola(idx)
        if asigno:
            self.last_b[i]["rnd"] = rnd_b
            self.last_b[i]["demora"] = demora_b
            self.last_b[i]["trx_rnd"] = trx_rnd
            self.last_b[i]["trx_tipo"] = trx_tipo

        self._update_biblio_estado()

        self.cli_perm_acum_total += event_perm_sum

        row = {
            "evento": f"FIN_ATENCION_{i}({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": "",
            "lleg_minuto": fmt(self.next_arrival, 2),
            "lleg_id": "",
            "trx_rnd": self.last_b[i]["trx_rnd"],
            "trx_tipo": self.last_b[i]["trx_tipo"],
            "lee_rnd": lee_rnd,
            "lee_lugar": lee_lugar,
            "lee_tiempo": lee_tiempo,
            "lee_fin": lee_fin,
            "b1_estado": self.bib[0].estado,
            "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.last_b[1]["demora"],
            "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado,
            "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.last_b[2]["demora"],
            "b2_hora": self.bib[1].hora,
            "cola": len(self.cola),
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self._total_people_present_for_display(),
            "est_b1_libre": fmt(self.last_iter_b1_libre),
            "est_b2_libre": fmt(self.last_iter_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.cli_perm_acum_total),
        }

        cli_snap = self.build_client_snapshot()
        return row, cli_snap

    def _evento_fin_lectura(self, cid):
        """
        FIN_LECTURA(cid):
        Cliente terminó de leer en sala y ahora debe devolver.
        """
        c = self.clientes[cid]
        t = c.fin_lect_num

        self._integrar_estadisticas_hasta(t)

        self.iteration += 1
        self.clock = t

        event_perm_sum = 0.0  # en FIN_LECTURA todavía no se destruye el cliente

        self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
        self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

        # pasa de leer a "Devolver"
        c.fin_lect_num = None
        c.cuando_termina_leer = ""
        c.accion_actual = "Devolver"
        self.biblio_personas_cnt = max(0, self.biblio_personas_cnt - 1)

        libre = self._primer_bib_libre()
        if libre is not None:
            c.estado = f"SA({libre + 1})"
            rnd_srv, demora = self._demora_por_transaccion(c.accion_actual)
            b = self.bib[libre]
            b.estado = "OCUPADO"
            b.rnd = fmt(rnd_srv)
            b.demora = fmt(demora)
            b.hora_num = self.clock + demora
            b.hora = fmt(b.hora_num)
            b.cliente_id = c.id

            self.last_b[libre + 1]["rnd"] = b.rnd
            self.last_b[libre + 1]["demora"] = b.demora
            self.last_b[libre + 1]["trx_rnd"] = ""
            self.last_b[libre + 1]["trx_tipo"] = "Devolver"
        else:
            c.estado = "EN COLA"
            c.hora_entrada_cola = self.clock
            self.cola.append(c.id)

        self._update_biblio_estado()

        self.cli_perm_acum_total += event_perm_sum  # suma 0

        row = {
            "evento": f"FIN_LECTURA({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": "",
            "lleg_minuto": fmt(self.next_arrival, 2),
            "lleg_id": "",
            "trx_rnd": "" if libre is None else self.last_b[libre + 1]["trx_rnd"],
            "trx_tipo": "" if libre is None else self.last_b[libre + 1]["trx_tipo"],
            "lee_rnd": "",
            "lee_lugar": "",
            "lee_tiempo": "",
            "lee_fin": "",
            "b1_estado": self.bib[0].estado,
            "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.bib[0].demora,
            "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado,
            "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.bib[1].demora,
            "b2_hora": self.bib[1].hora,
            "cola": len(self.cola),
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self._total_people_present_for_display(),
            "est_b1_libre": fmt(self.last_iter_b1_libre),
            "est_b2_libre": fmt(self.last_iter_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.cli_perm_acum_total),
        }

        cli_snap = self.build_client_snapshot()
        return row, cli_snap


# ----------------- Ventana de Estadísticas -----------------
class StatsWindow(tk.Toplevel):
    def __init__(self, master, engine: SimulationEngine):
        super().__init__(master)
        self.title("Estadísticas")
        self.geometry("360x210")
        self.resizable(False, False)
        self.engine = engine

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        self.lbl_cli = ttk.Label(frm, text="Clientes completados: 0")
        self.lbl_cli.pack(anchor="w")

        self.lbl_prom = ttk.Label(frm, text="Promedio permanencia: 0.00 min")
        self.lbl_prom.pack(anchor="w", pady=(2, 0))

        self.lbl_b1 = ttk.Label(frm, text="Ocioso B1: 0.00 min")
        self.lbl_b1.pack(anchor="w", pady=(2, 0))

        self.lbl_b2 = ttk.Label(frm, text="Ocioso B2: 0.00 min")
        self.lbl_b2.pack(anchor="w", pady=(2, 0))

        self.lbl_tot = ttk.Label(frm, text="Ocioso TOTAL: 0.00 min")
        self.lbl_tot.pack(anchor="w", pady=(2, 0))

        self.refresh()

    def refresh(self, final=False):
        stats = self.engine.finalizar_estadisticas() if final else self.engine.snapshot_estadisticas()
        self.lbl_cli.configure(text=f"Clientes completados: {stats['clientes_completados']}")
        self.lbl_prom.configure(text=f"Promedio permanencia: {stats['prom_permanencia']:.2f} min")
        self.lbl_b1.configure(text=f"Ocioso B1: {stats['b1_ocioso']:.2f} min")
        self.lbl_b2.configure(text=f"Ocioso B2: {stats['b2_ocioso']:.2f} min")
        self.lbl_tot.configure(text=f"Ocioso TOTAL: {stats['total_ocioso']:.2f} min")


# ----------------- Ventana de Simulación con tabla virtualizada -----------------
class SimulationWindow(tk.Toplevel):
    """
    Esta clase reemplaza el Treeview clásico.
    - Guarda cada fila en SQLite (no en memoria Python).
    - Dibuja sólo las filas visibles en un Canvas (tabla virtualizada).
    - Mantiene todas las filas accesibles con scroll vertical infinito.
    - Encabezado doble fijo (grupos arriba + nombres de columna abajo).
    """

    def __init__(self, master, config_dict):
        super().__init__(master)
        self.title("Vector de Estado - Simulación (Virtualizado / SQLite)")
        self.geometry("1400x760")
        self.minsize(1200, 560)

        # Motor de simulación
        self.engine = SimulationEngine(config_dict)
        self.modo_auto = bool(config_dict["simulacion"].get("modo_auto", False))

        self.stats_win = None
        self.known_clients = []  # clientes que ya generaron columnas dinámicas

        # --- DB temporal en disco (para no comer RAM con miles de filas) ---
        tmpfile = tempfile.NamedTemporaryFile(prefix="sim_", suffix=".db", delete=False)
        self._db_path = tmpfile.name
        tmpfile.close()
        self._db_conn = sqlite3.connect(self._db_path)
        self._init_db()
        self.total_rows = 0  # cuántas filas totales ya guardamos

        # Constantes de layout visual
        self.row_height = 24              # altura de cada fila dibujada
        self.header_h_group = 30          # alto fila "grupos"
        self.header_h_total = 60          # alto total header (grupos + nombres columnas)
        self.col_positions = []           # [(x0,x1), ...] acumulado según self.columns

        # --- FRAME raíz ---
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # --- Barra superior con config + botones ---
        top = ttk.Frame(root)
        top.pack(fill="x")

        resumen = ttk.Label(
            top,
            text=(
                f"Config → X={config_dict['simulacion']['tiempo_limite_min']} min | "
                f"N={config_dict['simulacion']['iteraciones_max']} | "
                f"i={config_dict['simulacion']['mostrar_vector_estado']['i_iteraciones']} "
                f"desde j={config_dict['simulacion']['mostrar_vector_estado']['desde_minuto_j']}  "
                f"| t_entre_llegadas={config_dict['llegadas']['tiempo_entre_llegadas_min']} min"
            ),
            foreground="#374151",
        )
        resumen.pack(side="left")

        ttk.Button(top, text="Estadísticas", command=self.open_stats).pack(side="right", padx=(6, 0))
        if not self.modo_auto:
            ttk.Button(top, text="Siguiente evento", command=self.on_next).pack(side="right")

        # --- Definición de columnas base y grupos de encabezado ---
        self.columns = []
        self.groups = []

        def add_col(cid, text, w):
            self.columns.append({"id": cid, "text": text, "w": w})

        # Grupo "" (iteración/evento/reloj)
        add_col("iteracion", "Numero de iteracion", 160)
        add_col("evento", "Evento", 180)
        add_col("reloj", "Reloj (minutos)", 130)

        # Grupo LLEGADA_CLIENTE
        add_col("lleg_tiempo", "TIEMPO", 90)
        add_col("lleg_minuto", "MINUTO QUE LLEGA", 165)
    

        # Grupo TRANSACCION
        add_col("trx_rnd", "RND", 80)
        add_col("trx_tipo", "Tipo Transaccion", 160)

        # Grupo ¿Dónde Lee?
        add_col("lee_rnd", "RND", 70)
        add_col("lee_lugar", "LUGAR", 110)
        add_col("lee_tiempo", "TIEMPO", 100)
        add_col("lee_fin", "Fin Lectura", 130)

        # Grupo BIBLIOTECARIO 1
        add_col("b1_estado", "Estado", 90)
        add_col("b1_rnd", "RND", 70)
        add_col("b1_demora", "Demora", 100)
        add_col("b1_hora", "Hora", 110)

        # Grupo BIBLIOTECARIO 2
        add_col("b2_estado", "Estado", 90)
        add_col("b2_rnd", "RND", 70)
        add_col("b2_demora", "Demora", 100)
        add_col("b2_hora", "Hora", 110)

        # Grupo COLA
        add_col("cola", "COLA", 90)

        # Grupo BIBLIOTECA
        add_col("biblio_estado", "Estado", 95)
        add_col("biblio_personas", "Personas en la biblioteca (MAXIMO 20)", 270)

        # Grupo ESTADISTICAS · BIBLIOTECARIOS
        add_col("est_b1_libre", "TIEMPO LIBRE BIBLIOTECARIO1", 230)
        add_col("est_b2_libre", "TIEMPO LIBRE BIBLIOTECARIO2", 230)
        add_col("est_bib_ocioso_acum", "ACUMULADOR TIEMPO OCIOSO BIBLIOTECARIOS", 330)

        # Grupo ESTADISTICAS · CLIENTES
        add_col("est_cli_perm_acum", "ACUMULADOR TIEMPO PERMANENCIA", 270)

        # Indices de grupos para header superior
        self.groups = [
            ("", 0, 2),
            ("LLEGADA_CLIENTE", 3, 5),
            ("TRANSACCION", 6, 7),
            ("¿Dónde Lee? - solo si pide Libro (cuenta luego de la atención)", 8, 11),
            ("BIBLIOTECARIO 1", 12, 15),
            ("BIBLIOTECARIO 2", 16, 19),
            ("COLA", 20, 20),
            ("BIBLIOTECA", 21, 22),
            ("ESTADISTICAS · BIBLIOTECARIOS", 23, 25),
            ("ESTADISTICAS · CLIENTES", 26, 26),
        ]

        # --- UI principal: header fijo + canvas scrollable ---
        wrapper = ttk.Frame(root)
        wrapper.pack(fill="both", expand=True)

        # header_canvas: dibuja grupos + nombres de columnas
        self.header_canvas = tk.Canvas(
            wrapper,
            height=self.header_h_total,
            background="#ffffff",
            highlightthickness=0
        )
        self.header_canvas.pack(fill="x", side="top")

        # body frame con canvas + scrollbar vertical
        body_frame = ttk.Frame(wrapper)
        body_frame.pack(fill="both", expand=True, side="left")

        self.body_canvas = tk.Canvas(
            body_frame,
            background="#ffffff",
            highlightthickness=0
        )
        self.body_canvas.pack(fill="both", expand=True, side="left")

        self.vscrollbar = ttk.Scrollbar(
            body_frame,
            orient="vertical",
            command=self._on_vscroll
        )
        self.vscrollbar.pack(fill="y", side="right")

        # scrollbar horizontal compartida por header + body
        self.hscrollbar = ttk.Scrollbar(
            root,
            orient="horizontal",
            command=self._on_hscroll
        )
        self.hscrollbar.pack(fill="x", side="bottom")

        # conectar scrollcommands
        self.body_canvas.configure(
            yscrollcommand=self._on_yview_changed,
            xscrollcommand=self.hscrollbar.set
        )
        self.header_canvas.configure(
            xscrollcommand=self.hscrollbar.set
        )

        # bindings para repintar cuando cambia tamaño o se hace scroll con rueda
        self.body_canvas.bind("<Configure>", self._on_body_configure)
        self.body_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.body_canvas.bind("<Button-4>", self._on_mousewheel_linux)   # Linux up
        self.body_canvas.bind("<Button-5>", self._on_mousewheel_linux)   # Linux down

        # calcular posiciones iniciales de columnas y dibujar headers
        self._recompute_columns_layout()

        # insertar fila inicial "INICIALIZACION" en la DB y renderizar
        self._insert_initialization_row()

        # si modo_auto está activo, disparamos toda la simulación
        if self.modo_auto:
            self.after(100, self.run_all_events)

        # liberar el archivo sqlite al cerrar
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- manejo de DB / scroll virtualizado ----------
    def _init_db(self):
        cur = self._db_conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS filas (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                data_json TEXT NOT NULL
            )
        """)
        self._db_conn.commit()

    def _on_close(self):
        try:
            self._db_conn.close()
        except Exception:
            pass
        try:
            os.remove(self._db_path)
        except Exception:
            pass
        self.destroy()

    def _update_scrollregion(self):
        """
        Ajusta las áreas de scroll de ambos canvas según
        - ancho total de columnas
        - cantidad total de filas
        """
        total_w = sum(c["w"] for c in self.columns)
        total_h_rows = self.total_rows * self.row_height

        self.header_canvas.configure(
            scrollregion=(0, 0, total_w, self.header_h_total)
        )
        self.body_canvas.configure(
            scrollregion=(0, 0, total_w, total_h_rows)
        )

    def _recompute_columns_layout(self):
        """
        Recalcula self.col_positions = [(x0,x1), ...] acumulando widths,
        actualiza scrollregion, redibuja header y filas visibles.
        """
        self.col_positions = []
        acc = 0
        for c in self.columns:
            x0 = acc
            x1 = acc + c["w"]
            self.col_positions.append((x0, x1))
            acc = x1

        self._update_scrollregion()
        self._draw_group_headers()
        self._redraw_visible_rows()

    def _draw_group_headers(self):
        """
        Dibuja:
        - Fila superior de grupos (LLEGADA_CLIENTE, TRANSACCION, etc.)
        - Fila inferior con los nombres de cada columna (Evento, Reloj, etc.)
        Ambas quedan fijas.
        """
        self.header_canvas.delete("all")

        xs = self.col_positions
        hg = self.header_h_group
        ht = self.header_h_total

        group_bg_color = GROUP_BG
        group_border_color = GROUP_BORDER
        fine_line_color = "#e5e7eb"
        group_separator_color = "#555555"

        group_boundaries = set()

        # 1) Cajas de grupo (fila superior)
        for text, i0, i1 in self.groups:
            if i0 >= len(xs) or i1 >= len(xs):
                continue
            x0 = xs[i0][0]
            x1 = xs[i1][1]

            # rectángulo del grupo
            self.header_canvas.create_rectangle(
                x0, 0, x1, hg,
                fill=group_bg_color,
                outline=group_border_color
            )

            if text:
                self.header_canvas.create_text(
                    (x0 + x1) / 2, hg / 2,
                    text=text,
                    anchor="center",
                    font=("Segoe UI", 9, "bold"),
                    fill="#000000"
                )

            # línea inferior de grupo
            self.header_canvas.create_line(
                x0, hg - 1, x1, hg - 1,
                fill=group_separator_color,
                width=1
            )
            group_boundaries.add(x0)
            group_boundaries.add(x1)

        # 2) Encabezado de cada columna (fila inferior)
        for col_idx, col in enumerate(self.columns):
            x0, x1 = xs[col_idx]
            # fondo de celda header de columna
            self.header_canvas.create_rectangle(
                x0, hg, x1, ht,
                fill="#f8fafc",
                outline="#d1d5db",
                width=1
            )
            # texto centrado
            self.header_canvas.create_text(
                (x0 + x1) / 2,
                hg + (ht - hg) / 2,
                text=col["text"],
                anchor="center",
                font=("Segoe UI", 8),
                fill="#000000"
            )

        # 3) Líneas verticales finas en límites de columnas
        for _, x1 in xs:
            self.header_canvas.create_line(
                x1, 0, x1, ht,
                fill=fine_line_color
            )

        # 4) remarcar bordes de grupo
        for x_boundary in sorted(list(group_boundaries)):
            if x_boundary == 0:
                continue
            self.header_canvas.create_line(
                x_boundary, 0,
                x_boundary, hg,
                fill=group_separator_color,
                width=1
            )

        # asegurar scrollregion del header
        self.header_canvas.configure(
            scrollregion=(0, 0, sum(c["w"] for c in self.columns), ht)
        )

    def _build_row_map(self, base_row: dict, cli_snap: dict, iteration_value: int):
        """
        Construye un dict {col_id: valor} alineado con self.columns actual.
        Esto es lo que guardaremos en SQLite.
        """
        row_map = {}
        for col in self.columns:
            col_id = col["id"]
            if col_id.startswith("c"):
                # columnas dinámicas de clientes: cX_estado, cX_hora_llegada, etc.
                parts = col_id.split("_", 1)
                prefix = parts[0]
                campo = parts[1] if len(parts) > 1 else ""
                cid_str = prefix[1:] if prefix.startswith("c") else prefix
                try:
                    cid_int = int(cid_str)
                except ValueError:
                    cid_int = None

                if cid_int is not None and cid_int in cli_snap:
                    cli_info = cli_snap[cid_int]
                    if campo == "estado":
                        row_map[col_id] = cli_info.get("estado", "")
                    elif campo == "hora_llegada":
                        row_map[col_id] = cli_info.get("hora_llegada", "")
                    elif campo == "a_que_fue":
                        row_map[col_id] = cli_info.get("a_que_fue", "")
                    elif campo == "cuando_termina":
                        row_map[col_id] = cli_info.get("cuando_termina", "")
                    else:
                        row_map[col_id] = ""
                else:
                    row_map[col_id] = ""
            else:
                if col_id == "iteracion":
                    row_map[col_id] = str(iteration_value)
                elif col_id == "cola":
                    # Siempre mostrar el largo de la cola como número, aunque sea 0
                    v = base_row.get("cola", 0)
                    row_map[col_id] = str(v)
                else:
                    v = base_row.get(col_id, "")
                    # si es None → "", si es "" → "", si es número → str(n)
                    if v is None:
                        row_map[col_id] = ""
                    elif v == "":
                        row_map[col_id] = ""
                    else:
                        row_map[col_id] = str(v)
        return row_map

    def _save_row_to_db(self, row_map: dict):
        """
        Inserta la fila en SQLite y actualiza contadores/scroll.
        """
        cur = self._db_conn.cursor()
        cur.execute(
            "INSERT INTO filas (data_json) VALUES (?)",
            (json.dumps(row_map),)
        )
        self._db_conn.commit()
        self.total_rows += 1
        self._update_scrollregion()

    def _fetch_rows_range(self, start_index: int, end_index: int):
        """
        Lee filas [start_index, end_index) desde SQLite,
        y las devuelve como lista de dicts.
        """
        if start_index < 0:
            start_index = 0
        if end_index > self.total_rows:
            end_index = self.total_rows
        limit = end_index - start_index
        if limit <= 0:
            return []

        cur = self._db_conn.cursor()
        cur.execute(
            "SELECT data_json FROM filas ORDER BY idx LIMIT ? OFFSET ?",
            (limit, start_index)
        )
        rows = []
        for (dj,) in cur.fetchall():
            rows.append(json.loads(dj))
        return rows

    def _redraw_visible_rows(self):
        """
        Borra las celdas dibujadas en body_canvas y vuelve a dibujar
        SOLO las filas visibles en pantalla según el scroll actual.
        """
        self.body_canvas.delete("rowcell")

        # coordenadas visibles actuales
        y0 = self.body_canvas.canvasy(0)
        h = self.body_canvas.winfo_height()
        if h <= 0:
            return

        first_row = int(y0 // self.row_height)
        last_row = int((y0 + h) // self.row_height) + 1

        # traemos de SQLite sólo ese rango
        visible_rows = self._fetch_rows_range(first_row, last_row)

        # dibujar cada fila
        for i, row_map in enumerate(visible_rows):
            row_idx = first_row + i
            y_top = row_idx * self.row_height
            y_bot = y_top + self.row_height

            bg = "#ffffff" if (row_idx % 2 == 0) else "#f9fafb"

            for col_idx, col in enumerate(self.columns):
                x0, x1 = self.col_positions[col_idx]
                # celda
                self.body_canvas.create_rectangle(
                    x0, y_top, x1, y_bot,
                    fill=bg,
                    outline="#d1d5db",
                    width=1,
                    tags="rowcell"
                )
                text_val = row_map.get(col["id"], "")
                self.body_canvas.create_text(
                    x0 + 4,
                    y_top + self.row_height / 2,
                    text=text_val,
                    anchor="w",
                    font=("Segoe UI", 9),
                    tags="rowcell"
                )

    def _insert_initialization_row(self):
        """
        Inserta la primera fila ("INICIALIZACION") en la DB y la dibuja.
        """
        eng = self.engine
        eng._update_biblio_estado()

        base = {
            "iteracion": 0,
            "evento": "INICIALIZACION",
            "reloj": fmt(eng.clock, 2),
            "lleg_tiempo": "",
            "lleg_minuto": fmt(eng.next_arrival, 2),
            "lleg_id": "",
            "trx_rnd": "",
            "trx_tipo": "",
            "lee_rnd": "",
            "lee_lugar": "",
            "lee_tiempo": "",
            "lee_fin": "",
            "b1_estado": "LIBRE",
            "b1_rnd": "",
            "b1_demora": "",
            "b1_hora": "",
            "b2_estado": "LIBRE",
            "b2_rnd": "",
            "b2_demora": "",
            "b2_hora": "",
            "cola": len(self.engine.cola),
            "biblio_estado": eng.biblio_estado,
            "biblio_personas": eng._total_people_present_for_display(),
            "est_b1_libre": fmt(0),
            "est_b2_libre": fmt(0),
            "est_bib_ocioso_acum": fmt(0),
            "est_cli_perm_acum": fmt(0),
        }

        # armamos row_map para TODAS las columnas actuales (no hay clientes aún)
        row_map = {}
        for col in self.columns:
            cid = col["id"]
            if cid == "iteracion":
                row_map[cid] = "0"
            elif cid == "cola":
                row_map[cid] = str(base.get("cola", 0))
            elif cid.startswith("c"):
                row_map[cid] = ""
            else:
                v = base.get(cid, "")
                if v is None or v == "":
                    row_map[cid] = ""
                else:
                    row_map[cid] = str(v)

        self._save_row_to_db(row_map)
        self._redraw_visible_rows()

    def _ensure_client_columns(self, cid: int):
        """
        Si aparece un cliente nuevo que nunca vimos antes,
        agregamos al final 4 columnas:
          c{cid}_estado,
          c{cid}_hora_llegada,
          c{cid}_a_que_fue,
          c{cid}_cuando_termina
        y creamos un grupo "Cliente {cid}" en el header.
        Luego recalculamos layout y redibujamos.
        """
        if cid in self.known_clients:
            return

        self.known_clients.append(cid)
        start_idx = len(self.columns)

        new_cols = [
            {"id": f"c{cid}_estado", "text": "ESTADO", "w": 110},
            {"id": f"c{cid}_hora_llegada", "text": "HORA_LLEGADA", "w": 130},
            {"id": f"c{cid}_a_que_fue", "text": "A QUE FUE", "w": 120},
            {"id": f"c{cid}_cuando_termina", "text": "Cuando termina de leer", "w": 180},
        ]
        self.columns.extend(new_cols)
        end_idx = len(self.columns) - 1

        # agregamos bloque de grupo visual
        self.groups.append((f"Cliente {cid}", start_idx, end_idx))

        # recalcular posiciones de columnas, scrollregions y headers
        self._recompute_columns_layout()

    def _process_event(self, row_base: dict, cli_snap: dict):
        """
        Paso común para on_next() y run_all_events():
        - asegura columnas de los clientes activos
        - arma el row_map en función del estado actual de columnas
        - guarda en DB
        - redibuja vista
        - refresca stats
        """
        # columnas dinámicas por cada cliente que aparece en esta iteración
        for cid in sorted(cli_snap.keys()):
            self._ensure_client_columns(cid)

        # armar fila en formato {col_id: valor}
        row_map = self._build_row_map(
            base_row=row_base,
            cli_snap=cli_snap,
            iteration_value=self.engine.iteration
        )

        # persistir en disco y actualizar scroll
        self._save_row_to_db(row_map)

        # redibujar filas visibles
        self._redraw_visible_rows()

        # refrescar ventana de estadísticas si está abierta
        self._refresh_stats_window(final=False)

    # ---------- Simulación ----------
    def run_all_events(self):
        """
        Ejecuta automáticamente todos los eventos restantes hasta que la simulación termine.
        """
        while True:
            try:
                if not self.engine.hay_mas():
                    # se acabó: integrar stats finales, mostrar alerta, abrir stats
                    self.engine.finalizar_estadisticas()
                    self.open_stats()
                    self._refresh_stats_window(final=True)
                    messagebox.showinfo(
                        "Fin de simulación",
                        "Se completó toda la simulación."
                    )
                    break

                row_base, cli_snap = self.engine.siguiente_evento()
                self._process_event(row_base, cli_snap)

            except StopIteration as e:
                self.open_stats()
                self._refresh_stats_window(final=True)
                messagebox.showinfo(
                    "Fin de simulación",
                    str(e)
                )
                break

    def on_next(self):
        """
        Avanza un solo evento (modo manual).
        """
        try:
            if not self.engine.hay_mas():
                self.engine.finalizar_estadisticas()
                self.open_stats()
                self._refresh_stats_window(final=True)
                messagebox.showinfo(
                    "Fin de simulación",
                    "No hay más eventos (límite de tiempo o iteraciones alcanzado)."
                )
                return

            row_base, cli_snap = self.engine.siguiente_evento()
            self._process_event(row_base, cli_snap)

        except StopIteration as e:
            self.open_stats()
            self._refresh_stats_window(final=True)
            messagebox.showinfo("Fin de simulación", str(e))

    # ---------- Helpers UI ----------
    def open_stats(self):
        if self.stats_win is None or not self.stats_win.winfo_exists():
            self.stats_win = StatsWindow(self, self.engine)
        else:
            self.stats_win.lift()
            self.stats_win.refresh(final=False)

    def _refresh_stats_window(self, final=False):
        if self.stats_win is not None and self.stats_win.winfo_exists():
            self.stats_win.refresh(final=final)

    # ---------- Scroll / eventos de canvas ----------
    def _on_vscroll(self, *args):
        """
        Scrollbar vertical movida → movemos canvas y repintamos filas visibles.
        """
        self.body_canvas.yview(*args)
        self._redraw_visible_rows()

    def _on_hscroll(self, *args):
        """
        Scrollbar horizontal movida → movemos ambos canvas (header y body)
        """
        self.header_canvas.xview(*args)
        self.body_canvas.xview(*args)
        # header no necesita redibujar; body sólo se desplaza en x.

    def _on_yview_changed(self, lo, hi):
        """
        Cuando el canvas cambia su yview (por mousewheel, etc.),
        actualizamos la scrollbar vertical y repintamos filas visibles.
        """
        self.vscrollbar.set(lo, hi)
        self._redraw_visible_rows()

    def _on_body_configure(self, event=None):
        """
        Cuando cambia el tamaño del canvas (ej. resize de ventana),
        redibujamos la parte visible.
        """
        self._redraw_visible_rows()

    def _on_mousewheel(self, event):
        """
        Scroll con rueda del mouse (Windows/macOS).
        """
        delta = int(-1 * (event.delta / 120))
        self.body_canvas.yview_scroll(delta, "units")
        self._redraw_visible_rows()

    def _on_mousewheel_linux(self, event):
        """
        Scroll con rueda en Linux (Button-4 / Button-5).
        """
        if event.num == 4:
            self.body_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.body_canvas.yview_scroll(1, "units")
        self._redraw_visible_rows()


# ----------------- Ventana Principal (input y validación) -----------------
class App(tk.Tk):
    """
    Pantalla de parámetros + botón "Generar".
    Esta parte es básicamente tu App original: pide X, N, porcentajes, etc.,
    arma el diccionario de config y abre SimulationWindow con esa config.
    """

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 680)

        self.style = ttk.Style(self)
        self.style.configure("Invalid.TEntry", fieldbackground="#ffe6e6")
        self.style.configure("Ok.TLabel", foreground="#15803d")
        self.style.configure("Bad.TLabel", foreground="#dc2626")

        # --- layout scrolleable del formulario de parámetros ---
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        main_frame = ttk.Frame(self)
        main_frame.grid(row=0, column=0, sticky="nsew")

        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(main_frame)
        self.scrollbar = ttk.Scrollbar(
            main_frame,
            orient="vertical",
            command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        root = ttk.Frame(self.canvas, padding=12)
        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=root,
            anchor="nw"
        )

        root.columnconfigure(0, weight=1)

        root.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind_all("<Button-4>", self.on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self.on_mousewheel_linux)

        # Campos
        self.fields = {}

        # --- 1) Simulación ---
        sim = ttk.LabelFrame(root, text="1) Simulación (todo en minutos)")
        sim.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sim.columnconfigure(1, weight=1)
        self._mk_int(
            sim, "tiempo_limite", "Tiempo límite X", 60, 1, 10_000,
            "La simulación termina al llegar a X o a N iteraciones (lo que ocurra primero)."
        )
        self._mk_int(
            sim, "iteraciones_max", "Cantidad de iteraciones N", 1000, 1, 100_000,
            "Máximo permitido: 100000."
        )
        self._mk_int(
            sim, "i_mostrar", "i (iteraciones a mostrar)", 10, 1, 100_000,
            "Cuántas iteraciones del vector de estado se listarán."
        )
        self._mk_int(
            sim, "j_inicio", "j (minuto de inicio)", 0, 0, 10_000,
            "Minuto desde el cual se comienzan a mostrar las i iteraciones."
        )

        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            sim,
            text="Ejecutar automáticamente (sin 'Siguiente evento')",
            variable=self.auto_var
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # --- 2) Llegadas ---
        lleg = ttk.LabelFrame(root, text="2) Llegadas")
        lleg.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        lleg.columnconfigure(1, weight=1)
        self._mk_int(
            lleg, "t_entre_llegadas", "Tiempo entre llegadas (min)", 4, 1, 10_000,
            "Entero en minutos (por defecto 4)."
        )

        # --- 3) Motivos ---
        motivos = ttk.LabelFrame(root, text="3) Motivos de llegada (%) — Debe sumar 100%")
        motivos.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        motivos.columnconfigure(1, weight=1)

        self._mk_int(motivos, "pct_pedir", "Pedir libros (%)", 45, 0, 100, on_change=self._update_pct_sum)
        self._mk_int(motivos, "pct_devolver", "Devolver libros (%)", 45, 0, 100, on_change=self._update_pct_sum)
        self._mk_int(motivos, "pct_consultar", "Consultar hacerse socio (%)", 10, 0, 100, on_change=self._update_pct_sum)

        sumrow = ttk.Frame(motivos)
        sumrow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(sumrow, text="Suma actual:").pack(side="left")
        self.lbl_sum = ttk.Label(sumrow, text="0%", style="Bad.TLabel")
        self.lbl_sum.pack(side="left", padx=6)

        # --- 4) Consultas (Uniforme A,B) ---
        cons = ttk.LabelFrame(root, text="4) Consultas — Distribución Uniforme(A, B) en minutos")
        cons.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        cons.columnconfigure(1, weight=1)

        self._mk_int(cons, "uni_a", "A (min)", 2, 0, 10_000,
                     "Debe cumplirse A < B y A ≠ B.")
        self._mk_int(cons, "uni_b", "B (min)", 5, 0, 10_000)

        # --- 5) Lectura ---
        lect = ttk.LabelFrame(root, text="5) Lectura")
        lect.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        lect.columnconfigure(1, weight=1)

        self._mk_int(
            lect, "pct_retira", "Se retira a leer en casa (%)", 60, 0, 100,
            on_change=self._update_queda
        )

        fila_queda = ttk.Frame(lect)
        fila_queda.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(fila_queda, text="Se queda a leer en biblioteca (%)").pack(side="left")
        self.lbl_queda = ttk.Label(fila_queda, text="40")
        self.lbl_queda.pack(side="left", padx=8)

        self._mk_int(
            lect, "t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 30, 1, 10_000,
            "Entero positivo (no 0)."
        )

        # --- 6) Resultado JSON ---
        salida = ttk.LabelFrame(root, text="6) Resultado")
        salida.grid(row=5, column=0, sticky="nsew", pady=(0, 8))
        salida.columnconfigure(0, weight=1)

        self.txt_out = tk.Text(salida, height=10)
        self.txt_out.grid(row=0, column=0, sticky="nsew")
        salida.rowconfigure(0, weight=1)

        # --- Botones ---
        btns = ttk.Frame(root)
        btns.grid(row=6, column=0, sticky="e")

        ttk.Button(btns, text="Restablecer", command=self.reset_defaults).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="Generar", command=self.on_generate).grid(row=0, column=1)

        # defaults iniciales
        self.reset_defaults()
        self._update_pct_sum()
        self._update_queda()

    # ---- helpers de UI principal (formulario) ----
    def _mk_int(self, parent, key, label, default, lo, hi, help_=None, on_change=None):
        row = ttk.Frame(parent)

        r = max(
            (child.grid_info().get("row", -1) for child in parent.winfo_children() if isinstance(child, ttk.Frame)),
            default=-1,
        ) + 1

        row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=3)
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text=label, width=34, anchor="w").grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=str(default))
        ent = ttk.Entry(row, textvariable=var, width=14)
        ent.grid(row=0, column=1, sticky="w", padx=(0, 8))

        if help_:
            ttk.Label(row, text=help_, foreground="#6b7280").grid(row=0, column=2, sticky="w")

        def only_digits(P):
            return (P == "") or P.isdigit()

        vcmd = (self.register(only_digits), "%P")
        ent.configure(validate="key", validatecommand=vcmd)

        self.fields[key] = {
            "var": var,
            "entry": ent,
            "lo": lo,
            "hi": hi,
            "default": default,
        }

        if on_change:
            var.trace_add("write", lambda *args: on_change())

        return ent

    def _update_pct_sum(self):
        s = 0
        for k in ("pct_pedir", "pct_devolver", "pct_consultar"):
            v = int_or_none(self.fields[k]["var"].get())
            s += v if v is not None else 0

        self.lbl_sum.configure(text=f"{s}%")
        if s == 100:
            self.lbl_sum.configure(style="Ok.TLabel")
        else:
            self.lbl_sum.configure(style="Bad.TLabel")

    def _update_queda(self):
        p = int_or_none(self.fields["pct_retira"]["var"].get())
        p = 0 if p is None else p
        queda = max(0, min(100, 100 - p))
        self.lbl_queda.configure(text=str(queda))

    # Scroll del formulario (pantalla de parámetros)
    def on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event=None):
        canvas_width = event.width
        self.canvas.itemconfig(self.canvas_window, width=canvas_width)

    def on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def on_mousewheel_linux(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")

    def reset_defaults(self):
        defaults = {
            "tiempo_limite": 60,
            "iteraciones_max": 1000,
            "i_mostrar": 10,
            "j_inicio": 0,
            "t_entre_llegadas": 4,
            "pct_pedir": 45,
            "pct_devolver": 45,
            "pct_consultar": 10,
            "uni_a": 2,
            "uni_b": 5,
            "pct_retira": 60,
            "t_lectura_biblio": 30,
        }

        for k, meta in self.fields.items():
            meta["entry"].configure(style="TEntry")
        for k, v in defaults.items():
            self.fields[k]["var"].set(str(v))

        self.txt_out.delete("1.0", "end")

    def on_generate(self):
        # limpiar estilos rojos
        for meta in self.fields.values():
            meta["entry"].configure(style="TEntry")

        errors = []
        mark = []

        def need_int(key, desc, lo, hi):
            val = int_or_none(self.fields[key]["var"].get())
            if not between(val, lo, hi):
                errors.append(f"• {desc}: debe ser entero en [{lo}, {hi}]")
                mark.append(key)
            return val

        t_lim = need_int("tiempo_limite", "Tiempo límite X", 1, 10_000)
        n_max = need_int("iteraciones_max", "Cantidad de iteraciones N", 1, 100_000)
        i_mos = need_int("i_mostrar", "i (iteraciones a mostrar)", 1, 100_000)
        j_ini = need_int("j_inicio", "j (minuto de inicio)", 0, 10_000)

        if None not in (t_lim, j_ini) and j_ini >= t_lim:
            errors.append("• j debe ser menor que X.")
            mark += ["j_inicio", "tiempo_limite"]

        if None not in (i_mos, n_max) and i_mos > n_max:
            errors.append("• i no debería exceder N.")
            mark += ["i_mostrar", "iteraciones_max"]

        t_lleg = need_int("t_entre_llegadas", "Tiempo entre llegadas (min)", 1, 10_000)

        p_ped = need_int("pct_pedir", "Pedir libros (%)", 0, 100)
        p_dev = need_int("pct_devolver", "Devolver libros (%)", 0, 100)
        p_con = need_int("pct_consultar", "Consultar hacerse socio (%)", 0, 100)

        if None not in (p_ped, p_dev, p_con):
            if p_ped + p_dev + p_con != 100:
                errors.append(f"• La suma de motivos debe ser 100% (ahora {p_ped+p_dev+p_con}%).")
                mark += ["pct_pedir", "pct_devolver", "pct_consultar"]

        a = need_int("uni_a", "Uniforme A (min)", 0, 10_000)
        b = need_int("uni_b", "Uniforme B (min)", 0, 10_000)

        if None not in (a, b):
            if a == b:
                errors.append("• En Uniforme(A,B) debe cumplirse A ≠ B.")
                mark += ["uni_a", "uni_b"]
            if a > b:
                errors.append("• En Uniforme(A,B) debe cumplirse A < B.")
                mark += ["uni_a", "uni_b"]

        p_ret = need_int("pct_retira", "Se retira a leer en casa (%)", 0, 100)
        t_bib = need_int("t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 1, 10_000)

        if errors:
            for k in set(mark):
                self.fields[k]["entry"].configure(style="Invalid.TEntry")
            messagebox.showerror("Validación", "Revisá:\n\n" + "\n".join(errors))
            return

        cfg = {
            "simulacion": {
                "tiempo_limite_min": t_lim,
                "iteraciones_max": n_max,
                "mostrar_vector_estado": {
                    "i_iteraciones": i_mos,
                    "desde_minuto_j": j_ini
                },
                "modo_auto": bool(self.auto_var.get())
            },
            "llegadas": {
                "tiempo_entre_llegadas_min": t_lleg
            },
            "motivos": {
                "pedir_libros_pct": p_ped,
                "devolver_libros_pct": p_dev,
                "consultar_socios_pct": p_con
            },
            "consultas_uniforme": {
                "a_min": a,
                "b_min": b
            },
            "lectura": {
                "retira_casa_pct": p_ret,
                "queda_biblioteca_pct": 100 - p_ret,
                "tiempo_fijo_biblioteca_min": t_bib
            }
        }

        # Mostrar config en el textbox y llevar al portapapeles
        self.txt_out.delete("1.0", "end")
        pretty = json.dumps(cfg, indent=2, ensure_ascii=False)
        self.txt_out.insert("1.0", pretty)
        self.clipboard_clear()
        self.clipboard_append(pretty)

        # Abrir la ventana de simulación con tabla virtualizada/SQLite
        SimulationWindow(self, cfg)


if __name__ == "__main__":
    App().mainloop()

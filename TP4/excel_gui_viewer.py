import tkinter as tk
from tkinter import ttk, messagebox
import json
import random
import math
from collections import deque
import sqlite3
import tempfile
import threading
import queue
import os

APP_TITLE = "Parámetros de Simulación - Biblioteca UTN - Grupo 8"
ROW_EVEN_BG = "#ffffff"      # fila par
ROW_ODD_BG = "#e5e7eb"       
ROW_SELECTED_BG = "#bfdbfe"  # azul suave para la fila seleccionada
ROW_SELECTED_FG = "#000000"

TREE_HEADER_BG = "#1f2937"   # gris oscuro
TREE_HEADER_FG = "#ffffff"   # texto blanco

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
    __slots__ = (
        "id",
        "estado",
        "hora_llegada",
        "hora_entrada_cola",
        "a_que_fue_inicial",
        "accion_actual",
        "fin_lect_num",
        "cuando_termina_leer",
    )

    def __init__(self, cid, hora_llegada):
        self.id = cid
        # estado puede ser:
        # "EN COLA", "SIENDO ATENDIDO(1)", "SIENDO ATENDIDO(2)", "LB", "DESTRUCCION"
        self.estado = "EN COLA"

        # Tiempos clave
        self.hora_llegada = hora_llegada  # float, necesitamos esto para permanencia total
        self.hora_entrada_cola = hora_llegada  # cuando entra o reingresa a cola

        # Motivo / acción
        self.a_que_fue_inicial = ""   # Pedir / Devolver / Consultar (primera vez que se define)
        self.accion_actual = ""       # acción actual que se está atendiendo

        # Lectura en biblioteca
        self.fin_lect_num = None      # float con el fin de lectura (si está leyendo en biblioteca)
        self.cuando_termina_leer = "" # string para mostrar "hh.hh" o mensaje


class Bibliotecario:
    __slots__ = ("estado", "rnd", "demora", "hora", "hora_num", "cliente_id")

    def __init__(self):
        self.estado = "LIBRE"     # "LIBRE" / "OCUPADO"
        self.rnd = ""             # RND del servicio asignado en ESTE evento
        self.demora = ""          # Demora asignada en ESTE evento
        self.hora = ""            # Fin de servicio estimado (string)
        self.hora_num = None      # Fin de servicio estimado (float)
        self.cliente_id = None    # ID del cliente que atiende ahora


# ----------------- Almacenamiento de filas (persistente, SQLite) -----------------
class RowStore:
    """
    Guarda filas de la simulación en una base SQLite temporal para no mantenerlas todas en RAM.
    Cada fila almacena: iteration (int), row_json (text), cli_snap_json (text)
    """
    def __init__(self, path=None):
        self._owns_file = False
        if path is None:
            fd, path = tempfile.mkstemp(prefix="tp4_rows_", suffix=".db")
            os.close(fd)
            self._owns_file = True
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                iteration INTEGER,
                row_json TEXT,
                cli_json TEXT
            )
            """
        )
        self.conn.commit()

    def add_row(self, iteration, row, cli_snap):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO rows (iteration, row_json, cli_json) VALUES (?, ?, ?)",
            (iteration, json.dumps(row, ensure_ascii=False), json.dumps(cli_snap, ensure_ascii=False)),
        )
        if iteration % 100 == 0:
            # flush reasonably often
            self.conn.commit()

    def finalize(self):
        self.conn.commit()

    def count(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rows")
        return cur.fetchone()[0]

    def fetch_range(self, offset, limit):
        cur = self.conn.cursor()
        cur.execute("SELECT iteration, row_json, cli_json FROM rows ORDER BY id LIMIT ? OFFSET ?", (limit, offset))
        for it, rj, cj in cur.fetchall():
            yield it, json.loads(rj), json.loads(cj)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        if self._owns_file:
            try:
                os.remove(self.path)
            except Exception:
                pass


# ----------------- Motor de simulación -----------------
class SimulationEngine:
    """
    Motor de eventos discretos. Cada avance:
      - LLEGADA_CLIENTE
      - FIN_ATENCION_i
      - FIN_LECTURA

    Lógica clave pedida:
    - El tiempo libre de cada bibliotecario en la fila actual es SOLO el intervalo entre el evento anterior y éste
      (si estuvo libre todo ese intervalo, vale ese dt; si no, vale 0).
    - El acumulador de tiempo ocioso de bibliotecarios es ACUMULADO histórico:
      acumulado_prev + (libre_b1_iter + libre_b2_iter).
    - El acumulador de permanencia de clientes se incrementa SOLO cuando el cliente entra en DESTRUCCION
      (sale del sistema). El valor que se suma es reloj_actual - hora_llegada. Se muestra acumulado histórico.
    """

    def __init__(self, cfg, store=None):
        self.cfg = cfg
        self.store = store

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

        # Estructuras de estado del sistema
        self.cola = deque()            # cola FIFO de IDs de cliente
        self.clientes = {}             # id -> Cliente (solo vivos / activos / recién destruidos)
        self._to_clear_after_emit = set()  # IDs que se borran ANTES del siguiente evento

        # Bibliotecarios
        self.bib = [Bibliotecario(), Bibliotecario()]

        # Gente leyendo físicamente en sala
        self.biblio_personas_cnt = 0
        self.biblio_estado = ""
        self._update_biblio_estado()

        # Valores que se muestran SOLO en la fila actual (por bibliotecario)
        self.last_b = {
            1: {"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""},
            2: {"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""},
        }

        # Métricas acumuladas GLOBALES históricas
        # - est_b1_libre_acum / est_b2_libre_acum:
        #   tiempo total que cada bibliotecario estuvo libre sumando todas las iteraciones.
        # - est_bib_ocioso_acum:
        #   suma acumulada total entre ambos bibliotecarios. (lo que vos querés en la columna ACUMULADOR...)
        self.est_b1_libre_acum = 0.0
        self.est_b2_libre_acum = 0.0
        self.est_bib_ocioso_acum = 0.0

        # Valores por-iteración (para mostrar columnas TIEMPO LIBRE B1 / B2 en esa fila)
        self.last_iter_b1_libre = 0.0
        self.last_iter_b2_libre = 0.0

        # Acumulador histórico de permanencia de clientes destruidos
        # (ACUMULADOR TIEMPO PERMANENCIA en la tabla)
        self.cli_perm_acum_total = 0.0

        # También llevamos métrica para promedio final:
        # sum_tiempo_en_sistema y cli_completados, sólo cuando un cliente se destruye
        self.cli_completados = 0
        self.sum_tiempo_en_sistema = 0.0

        self._finalizado = False

    # ----------------- helpers internos -----------------
    def _clear_destroyed_clients(self):
        """
        Borra definitivamente (de self.clientes) los que ya salieron en el evento anterior.
        Así en la fila siguiente dejan de aparecer en las columnas Cliente N_*.
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
        A partir de un rnd en [0,1):
        - si cae en pedir -> 'Pedir'
        - si cae en devolver -> 'Devolver'
        - si cae en consultar -> 'Consultar'
        """
        if rnd_val < self.p_pedir:
            return "Pedir"
        elif rnd_val < self.p_pedir + self.p_devolver:
            return "Devolver"
        else:
            return "Consultar"

    def _sortear_transaccion_si_falta(self, cliente: Cliente):
        """
        Si el cliente todavía no tiene acción_actual,
        sorteamos su primera transacción y la guardamos.
        Devuelve (rnd_trx, tipo_trx) como strings para registrar en la fila.
        """
        if cliente.accion_actual:
            # Ya traía una acción en curso (ej., volvió de leer y ahora viene a "Devolver")
            return "", cliente.accion_actual

        rnd_trx_val = random.random()
        tipo = self._elige_transaccion(rnd_trx_val)
        cliente.a_que_fue_inicial = tipo
        cliente.accion_actual = tipo
        return fmt(rnd_trx_val, 4), tipo

    def _demora_por_transaccion(self, tipo):
        """
        Devuelve (rnd_servicio, demora) según la acción que atiende el bibliotecario.
        - Consultar: Uniforme(A,B)
        - Devolver: Uniforme(1.5, 2.5) (ejemplo)
        - Pedir: Exponencial(media=6)
        """
        r = random.random()
        if tipo == "Consultar":
            demora = self.uni_a + (self.uni_b - self.uni_a) * r
        elif tipo == "Devolver":
            demora = 1.5 + r * (2.5 - 1.5)
        else:  # "Pedir"
            demora = -6.0 * math.log(1.0 - r)  # Exponencial media=6
        return r, demora

    def _tomar_de_cola(self, idx_bib):
        """
        Si hay alguien en cola y el bibliotecario idx_bib está libre,
        lo atiende inmediatamente.

        Devuelve tuple:
          (hubo_asignacion, rnd_serv, demora, trx_rnd, trx_tipo)
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
        Cantidad de clientes ocupando lugar físico en biblioteca:
        - cola
        - siendo atendidos
        - leyendo en sala
        """
        en_servicio = (1 if self.bib[0].estado == "OCUPADO" else 0) + \
                      (1 if self.bib[1].estado == "OCUPADO" else 0)
        return len(self.cola) + en_servicio + self.biblio_personas_cnt

    def _total_people_present_for_display(self):
        """
        Total de personas físicas adentro:
        2 bibliotecarios + clientes en cola/atención/leyendo.
        """
        return 2 + self._current_clients_occupying_spot()

    def _update_biblio_estado(self):
        """
        Biblioteca Abierta o Cerrada según capacidad.
        """
        if self._total_people_present_for_display() >= MAX_CAPACITY:
            self.biblio_estado = "Cerrada"
        else:
            self.biblio_estado = "Abierta"

    def _integrar_estadisticas_hasta(self, new_time: float):
        """
        Integra las métricas desde self.last_clock hasta new_time.

        - Calcula dt.
        - Si un bibliotecario estuvo LIBRE TODO ese dt, ese dt se considera
          "tiempo libre de ESTA iteración" para ese bib.
          Caso contrario, 0 para ese bib en ESTA iteración.
        - Suma esos dt al acumulador histórico.
        - Actualiza el acumulador total de ocio de ambos.
        """
        dt = new_time - self.last_clock

        # Reset valores por-iteración (para esta fila)
        self.last_iter_b1_libre = 0.0
        self.last_iter_b2_libre = 0.0

        if dt <= 0:
            self.last_clock = new_time
            # no pasa tiempo → en esta iteración ambos libres = 0 y no sumamos
            return

        # Bibliotecario 1
        if self.bib[0].estado == "LIBRE":
            self.last_iter_b1_libre = dt
            self.est_b1_libre_acum += dt  # histórico global

        # Bibliotecario 2
        if self.bib[1].estado == "LIBRE":
            self.last_iter_b2_libre = dt
            self.est_b2_libre_acum += dt  # histórico global

        # Actualizamos acumulador histórico total de ocio (B1+B2)
        self.est_bib_ocioso_acum = self.est_b1_libre_acum + self.est_b2_libre_acum

        # Avanzamos marcador temporal
        self.last_clock = new_time

    def _proximo_evento(self):
        """
        Devuelve el próximo evento como tupla (t, prioridad, tipo, data).
        Prioridad para desempatar:
          1 FIN_ATENCION_1
          2 FIN_ATENCION_2
          3 FIN_LECTURA (offset por ID)
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
            if c.estado == "LB" and c.fin_lect_num is not None:
                cand.append((c.fin_lect_num, 3 + cid * 1e-6, "fin_lectura", {"cid": cid}))

        # Próxima llegada
        if self.next_arrival is not None:
            cand.append((self.next_arrival, 4, "llegada", {}))

        if not cand:
            return None

        return min(cand, key=lambda x: (x[0], x[1]))

    def hay_mas(self):
        """
        ¿Quedan eventos dentro de límites?
        """
        self._clear_destroyed_clients()

        ne = self._proximo_evento()
        if ne is None:
            return False
        t, *_ = ne
        return (self.iteration < self.iter_limit) and (t <= self.time_limit)

    # ---------- snapshots / métricas para la UI ----------
    def build_client_snapshot(self):
            """
            Snapshot para las columnas dinámicas Cliente N.

            Reglas de visualización por estado:

            - "DESTRUCCION":
                Solo mostramos el estado.
                Dejamos vacíos hora_llegada, a_que_fue y cuando_termina.
                (El cliente ya salió del sistema.)

            - "LB" (leyendo en biblioteca):
                Mostramos estado, hora_llegada y cuando_termina.
                PERO dejamos vacío "a_que_fue", porque en esta etapa
                ya no nos importa el motivo original con el que vino.

            - Otros estados ("EN COLA", "SA(1)", "SA(2)", etc.):
                Mostramos todo normalmente.
            """
            snap = {}
            for cid, c in self.clientes.items():
                if c.estado == "DESTRUCCION":
                    snap[cid] = {
                        "estado": c.estado,
                        "hora_llegada": "",
                        "a_que_fue": "",
                        "cuando_termina": "",
                    }

                elif c.estado == "LB":
                    snap[cid] = {
                        "estado": c.estado,
                        "hora_llegada": fmt(c.hora_llegada, 2),
                        "a_que_fue": "",  # <- pedido: no mostrar el "a qué fue" en LB
                        "cuando_termina": c.cuando_termina_leer,
                    }

                else:
                    snap[cid] = {
                        "estado": c.estado,
                        "hora_llegada": fmt(c.hora_llegada, 2),
                        "a_que_fue": c.accion_actual or c.a_que_fue_inicial,
                        "cuando_termina": c.cuando_termina_leer,
                    }

            return snap
    


    def snapshot_estadisticas(self):
        """
        Datos para la ventanita de Estadísticas (promedios globales).
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
        Integra hasta time_limit si quedaba un tramo final.
        """
        if not self._finalizado and self.last_clock < self.time_limit:
            self._integrar_estadisticas_hasta(self.time_limit)
        self._finalizado = True
        return self.snapshot_estadisticas()

    # ---------- EVENTOS PRINCIPALES ----------
    def siguiente_evento(self):
        """
        Avanza 1 evento y devuelve:
         - row_dict (para columnas base de la fila nueva)
         - cli_snap (para columnas Cliente N)
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

        # Integramos estadística de ocio desde last_clock hasta t
        self._integrar_estadisticas_hasta(t)

        # Avanzamos
        self.iteration += 1
        self.clock = t

        # Acumulador parcial de permanencias SOLO de los que salen en ESTE evento
        event_perm_sum = 0.0

        # Limpiamos registros de bibliotecarios que mostramos solo en ESTA fila
        self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
        self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

        # Creamos nuevo cliente
        cid = self.next_client_id
        self.next_client_id += 1
        c = Cliente(cid, hora_llegada=self.clock)

        trx_rnd = ""
        trx_tipo = ""

        # Chequeo de capacidad física
        if self._current_clients_occupying_spot() >= (MAX_CAPACITY - 2):
            # No entra → destruido inmediatamente
            c.estado = "DESTRUCCION"
            c.fin_lect_num = None
            c.cuando_termina_leer = "CLIENTE DESTRUIDO (CAPACIDAD MAXIMA)"
            self.clientes[cid] = c

            # Tiempo de permanencia = reloj actual - hora_llegada
            tiempo_perm = (self.clock - c.hora_llegada)
            event_perm_sum += tiempo_perm

            # Para estadísticas globales finales
            self.sum_tiempo_en_sistema += tiempo_perm
            self.cli_completados += 1

            # Se eliminará de memoria en la próxima iteración
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

                # Para mostrar SOLO en esta fila
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

        # Programo próxima llegada
        self.next_arrival = self.clock + self.t_inter

        # Actualizo estado de biblioteca
        self._update_biblio_estado()

        # >>>>> acumulador histórico de permanencia de clientes <<<<<
        # Sumo al acumulador global SOLO lo que salió en este evento
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
            # --- estadísticas solicitadas en la tabla ---
            # Libre por iteración (dt de ESTA iteración, o 0)
            "est_b1_libre": fmt(self.last_iter_b1_libre),
            "est_b2_libre": fmt(self.last_iter_b2_libre),
            # Acumulador histórico total de ocio (B1+B2)
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            # Acumulador histórico de permanencia clientes destruidos
            "est_cli_perm_acum": fmt(self.cli_perm_acum_total),
        }

        cli_snap = self.build_client_snapshot()
        if getattr(self, 'store', None) is not None:
            try:
                self.store.add_row(self.iteration, row, cli_snap)
            except Exception:
                pass
        return row, cli_snap

    def _evento_fin_atencion(self, i):
            """
            Evento: FIN_ATENCION_i
            """
            idx = i - 1
            b = self.bib[idx]
            t = b.hora_num

            # Integramos ocio hasta este tiempo
            self._integrar_estadisticas_hasta(t)

            # Avanzamos
            self.iteration += 1
            self.clock = t

            # Permanencia de los clientes que salen en ESTE evento
            event_perm_sum = 0.0

            # Reset columnas de bibliotecarios para ESTA fila
            self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
            self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

            cid = b.cliente_id
            c = self.clientes[cid]

            # Campos que van al bloque "¿Dónde Lee?" de la fila
            lee_rnd = ""
            lee_lugar = ""
            lee_tiempo = ""
            lee_fin = ""

            # Después de la atención, depende de la acción
            if c.accion_actual == "Pedir":
                # Decide si se lo lleva o se queda leyendo
                r = random.random()
                lee_rnd = fmt(r, 4)

                if r < self.p_retira:
                    # CASO 1: Se lo lleva para leer en su casa
                    # → pasa directo a destrucción
                    c.estado = "DESTRUCCION"
                    c.fin_lect_num = None
                    c.cuando_termina_leer = ""

                    # NUEVO: marcar explícitamente dónde lee
                    # aunque ya se vaya del sistema
                    lee_lugar = "Casa"
                    lee_tiempo = ""
                    lee_fin = ""

                    # estadística de permanencia
                    tiempo_perm = (self.clock - c.hora_llegada)
                    event_perm_sum += tiempo_perm
                    self.sum_tiempo_en_sistema += tiempo_perm
                    self.cli_completados += 1
                    self._to_clear_after_emit.add(c.id)

                else:
                    # CASO 2: Se queda a leer en biblioteca
                    c.estado = "LB"
                    fin_lec = self.clock + self.t_lect_biblio
                    c.fin_lect_num = fin_lec
                    c.cuando_termina_leer = fmt(fin_lec, 2)

                    lee_lugar = "Biblioteca"
                    lee_tiempo = fmt(self.t_lect_biblio, 2)
                    lee_fin = c.cuando_termina_leer

                    # ahora ocupa una mesa en sala de lectura
                    self.biblio_personas_cnt += 1

            else:
                # Devolver / Consultar ⇒ se va del sistema
                c.estado = "DESTRUCCION"
                c.fin_lect_num = None
                c.cuando_termina_leer = ""

                # En este caso NO vino a leer nada, así que no ponemos "Casa"
                # ni "Biblioteca", lo dejamos vacío.
                # (Mantiene la semántica: solo "Casa" aplica a "me llevo el libro a casa")
                tiempo_perm = (self.clock - c.hora_llegada)
                event_perm_sum += tiempo_perm
                self.sum_tiempo_en_sistema += tiempo_perm
                self.cli_completados += 1
                self._to_clear_after_emit.add(c.id)

            # Bibliotecario queda libre
            b.estado = "LIBRE"
            b.rnd = ""
            b.demora = ""
            b.hora = ""
            b.hora_num = None
            b.cliente_id = None

            # Intenta agarrar siguiente en cola
            asigno, rnd_b, demora_b, trx_rnd, trx_tipo = self._tomar_de_cola(idx)
            if asigno:
                self.last_b[i]["rnd"] = rnd_b
                self.last_b[i]["demora"] = demora_b
                self.last_b[i]["trx_rnd"] = trx_rnd
                self.last_b[i]["trx_tipo"] = trx_tipo

            # Actualizamos estado biblioteca
            self._update_biblio_estado()

            # >>>>> acumulador histórico de permanencia de clientes <<<<<
            self.cli_perm_acum_total += event_perm_sum

            row = {
                "evento": f"FIN_ATENCION_{i}({cid})",
                "reloj": fmt(self.clock, 2),
                "lleg_tiempo": "",
                "lleg_minuto": fmt(self.next_arrival, 2),
                "lleg_id": "",
                "trx_rnd": self.last_b[i]["trx_rnd"],
                "trx_tipo": self.last_b[i]["trx_tipo"],

                # ¿Dónde Lee?
                "lee_rnd": lee_rnd,
                "lee_lugar": lee_lugar,     # <- ahora puede ser "Biblioteca" o "Casa"
                "lee_tiempo": lee_tiempo,   # si "Casa", queda ""
                "lee_fin": lee_fin,         # si "Casa", queda ""

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

                # estadísticas pedidas:
                "est_b1_libre": fmt(self.last_iter_b1_libre),
                "est_b2_libre": fmt(self.last_iter_b2_libre),
                "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
                "est_cli_perm_acum": fmt(self.cli_perm_acum_total),
            }

            cli_snap = self.build_client_snapshot()
            if getattr(self, 'store', None) is not None:
                try:
                    self.store.add_row(self.iteration, row, cli_snap)
                except Exception:
                    pass
            return row, cli_snap


    def _evento_fin_lectura(self, cid):
        """
        FIN_LECTURA(cid):
        El cliente terminó de leer en la sala y ahora debe devolver.
        """
        c = self.clientes[cid]
        t = c.fin_lect_num

        # Integramos ocio hasta este tiempo
        self._integrar_estadisticas_hasta(t)

        # Avanzamos
        self.iteration += 1
        self.clock = t

        # Permanencia aportada por salidas en ESTE evento:
        # En FIN_LECTURA nadie se destruye directamente todavía,
        # así que en esta iteración será 0.
        event_perm_sum = 0.0

        self.last_b[1].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})
        self.last_b[2].update({"rnd": "", "demora": "", "trx_rnd": "", "trx_tipo": ""})

        # pasa de leer a devolver
        c.fin_lect_num = None
        c.cuando_termina_leer = ""
        c.accion_actual = "Devolver"
        # Ya no ocupa mesa de lectura
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

        # En FIN_LECTURA nadie fue destruido todavía,
        # así que el acumulador histórico de permanencia NO aumenta
        self.cli_perm_acum_total += event_perm_sum  # suma 0 igual, para claridad

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
        if getattr(self, 'store', None) is not None:
            try:
                self.store.add_row(self.iteration, row, cli_snap)
            except Exception:
                pass
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


# ----------------- Ventana de Simulación (Vector de Estado) -----------------
class SimulationWindow(tk.Toplevel):
    
    def run_all_events(self):
        """Lanza un worker en background que genera filas (engine.siguiente_evento)
        y las pone en una cola. La UI consume la cola por lotes para mantener
        la interfaz responsiva y evitar bloquear el hilo principal.
        """
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def worker():
            try:
                while True:
                    if not self.engine.hay_mas():
                        # Finalizamos estadísticas y salimos
                        try:
                            self.engine.finalizar_estadisticas()
                        except Exception:
                            pass
                        break

                    try:
                        row, cli_snap = self.engine.siguiente_evento()
                    except StopIteration:
                        break

                    # push to queue (worker -> ui)
                    self._ui_queue.put((row, cli_snap, self.engine.iteration))

            finally:
                # Indicar fin con sentinel
                self._ui_queue.put(None)
                try:
                    self.store.finalize()
                except Exception:
                    pass

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

        # Programamos la rutina que procesa la cola y actualiza la UI
        self.after(50, self._process_queue)

    
    def __init__(self, master, config_dict):
        super().__init__(master)
        self.title("Vector de Estado - Simulación (Streaming memoria optimizada)")
        self.geometry("1400x760")
        self.minsize(1200, 560)

        # Creamos almacenamiento persistente de filas y el motor usando ese store
        self.store = RowStore()
        self.engine = SimulationEngine(config_dict, store=self.store)
        self.modo_auto = bool(config_dict["simulacion"].get("modo_auto", False))
        self.stats_win = None
        self.known_clients = []  # clientes que ya generaron columnas

        # Cola para comunicación worker -> UI
        self._ui_queue = queue.Queue()
        self._worker_thread = None
        self._worker_done = False

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # Barra superior
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
            # Solo en modo manual mostramos "Siguiente evento"
            ttk.Button(top, text="Siguiente evento", command=self.on_next).pack(side="right")
        else:
            # En modo auto podés querer un botón para "Pausar" o "Ejecutar todo ahora".
            # Si querés, dejalo así de simple y que corra solo:
            pass


        # Definición de columnas base
        self.columns = []
        self.groups = []

        def add_col(cid, text, w):
            self.columns.append({"id": cid, "text": text, "w": w})

        # Grupo "" (iteración / evento / reloj)
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
        # TIEMPO LIBRE B1 / B2 -> solo en ESTA iteración
        # ACUMULADOR TIEMPO OCIOSO BIBLIOTECARIOS -> acumulado histórico total
        add_col("est_b1_libre", "TIEMPO LIBRE BIBLIOTECARIO 1", 230)
        add_col("est_b2_libre", "TIEMPO LIBRE BIBLIOTECARIO2", 230)
        add_col("est_bib_ocioso_acum", "ACUMULADOR TIEMPO OCIOSO BIBLIOTECARIOS", 330)

        # Grupo ESTADISTICAS · CLIENTESa
        # ACUMULADOR TIEMPO PERMANENCIA -> acumulado histórico de (reloj actual - hora_llegada)
        # SOLO cuando el cliente pasa a DESTRUCCION
        add_col("est_cli_perm_acum", "ACUMULADOR TIEMPO PERMANENCIA", 270)

        # Índices de grupos para el header de arriba
        self.groups = [
            ("", 0, 2),
            ("LLEGADA_CLIENTE", 3, 5),
            ("TRANSACCION", 6, 7),
            ("¿Dónde Lee? - solo si pide Libro", 8, 11),
            ("BIBLIOTECARIO 1", 12, 15),
            ("BIBLIOTECARIO 2", 16, 19),
            ("COLA", 20, 20),
            ("BIBLIOTECA", 21, 22),
            ("ESTADISTICAS · BIBLIOTECARIOS", 23, 25),
            ("ESTADISTICAS · CLIENTES", 26, 26),
        ]

        # --- UI: canvas de encabezado de grupos + Treeview ---
        wrapper = ttk.Frame(root)
        wrapper.pack(fill="both", expand=True)

        self.header_canvas = tk.Canvas(
            wrapper,
            height=40,  # más alto para que se vea mejor
            background="#ffffff",
            highlightthickness=0
        )
        self.header_canvas.pack(fill="x", side="top")

        self.tree = ttk.Treeview(wrapper, show="headings", height=20)
        self.tree.pack(fill="both", expand=True, side="left")

        self.tree.tag_configure('evenrow', background=ROW_EVEN_BG)
        self.tree.tag_configure('oddrow', background=ROW_ODD_BG)

        yscroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        yscroll.pack(fill="y", side="right")

        xscroll = ttk.Scrollbar(root, orient="horizontal")
        xscroll.pack(fill="x", side="bottom")

        def on_xscroll(*args):
            # mover ambos: tabla + header de grupos
            self.tree.xview(*args)
            self.header_canvas.xview(*args)

        def on_tree_xscroll(lo, hi):
            xscroll.set(lo, hi)
            self.header_canvas.xview_moveto(lo)

        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=on_tree_xscroll)
        xscroll.configure(command=on_xscroll)

        # Aplicar columnas al Treeview y dibujar encabezados
        self._apply_columns()
        self._draw_group_headers()

        # Inserto fila de INICIALIZACION
        self._insert_initialization_row()
        # Ejecutar toda la simulación automáticamente si así se configuró
        if self.modo_auto:
            # Dejamos respirar a la UI y luego lanzamos el worker
            self.after(100, self.run_all_events)


    # --- Helpers UI ---
    def open_stats(self):
        if self.stats_win is None or not self.stats_win.winfo_exists():
            self.stats_win = StatsWindow(self, self.engine)
        else:
            self.stats_win.lift()
            self.stats_win.refresh(final=False)

    def _refresh_stats_window(self, final=False):
        if self.stats_win is not None and self.stats_win.winfo_exists():
            self.stats_win.refresh(final=final)

    def _process_queue(self):
        """Procesa la cola de filas producidas por el worker e inserta en el Treeview por lotes."""
        batch = []
        while len(batch) < 200:
            try:
                item = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            batch.append(item)

        for item in batch:
            if item is None:
                # sentinel: worker terminado
                self._worker_done = True
                # Abrir stats finales
                try:
                    self.open_stats()
                    self._refresh_stats_window(final=True)
                except Exception:
                    pass
                messagebox.showinfo("Fin de simulación", "Se completó toda la simulación.")
                continue

            row, cli_snap, iteration = item

            # Asegurar columnas de todos los clientes activos
            for cid in sorted(cli_snap.keys()):
                self._ensure_client_columns(cid)

            # Armar fila completa
            values = []
            for col_id in self.tree["columns"]:
                if self._is_client_column(col_id):
                    try:
                        prefix, campo = col_id.split("_", 1)
                    except ValueError:
                        prefix, campo = col_id, ""
                    cid_str = prefix[1:] if prefix.startswith("c") else prefix
                    try:
                        cid_int = int(cid_str)
                    except ValueError:
                        cid_int = None

                    if cid_int is not None and cid_int in cli_snap:
                        cli_info = cli_snap[cid_int]
                        values.append(cli_info.get(campo, ""))
                    else:
                        values.append("")
                else:
                    if col_id == "iteracion":
                        values.append(str(iteration))
                    else:
                        v = row.get(col_id, "")
                        values.append("" if v == "" else str(v))

            tag = 'evenrow' if iteration % 2 == 0 else 'oddrow'
            self.tree.insert("", "end", values=values, tags=(tag,))

        # Refrescar UI y stats
        if batch:
            self._draw_group_headers()
            self._refresh_stats_window(final=False)

        # Seguir procesando si el worker no terminó
        if not self._worker_done:
            self.after(50, self._process_queue)

    def _apply_columns(self):
        """
        Crea las headings del Treeview en base a self.columns actual.
        """
        col_ids = [c["id"] for c in self.columns]
        self.tree["columns"] = col_ids

        for c in self.columns:
            self.tree.heading(c["id"], text=c["text"], anchor="center")
            self.tree.column(
                c["id"],
                width=c["w"],
                minwidth=40,
                anchor="center",
                stretch=False
            )

        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), 40))

    def _total_width(self):
        total = 0
        for c in self.columns:
            total += self.tree.column(c["id"], option="width")
        return total

    def _col_x_positions(self):
        xs = []
        acc = 0
        for c in self.columns:
            w = self.tree.column(c["id"], option="width")
            xs.append((acc, acc + w))
            acc += w
        return xs

    def _draw_group_headers(self):
        """
        Dibuja la línea superior con los grupos:
        LLEGADA_CLIENTE, TRANSACCION, Cliente 1, Cliente 2, etc.
        Llamamos esto:
          - al iniciar
          - al agregar columnas de un cliente nuevo
          - al final de cada on_next()
        para que SIEMPRE se vea el encabezado.
        """
        self.header_canvas.delete("all")
        xs = self._col_x_positions()
        h = 40

        group_bg_color = GROUP_BG
        group_border_color = GROUP_BORDER
        fine_line_color = "#e5e7eb"
        group_separator_color = "#555555"
        group_boundaries = set()

        for text, i0, i1 in self.groups:
            if i0 >= len(xs) or i1 >= len(xs):
                continue
            x0 = xs[i0][0]
            x1 = xs[i1][1]

            # caja del grupo
            self.header_canvas.create_rectangle(
                x0, 0, x1, h,
                fill=group_bg_color,
                outline=group_border_color
            )
            # título del grupo
            if text:
                self.header_canvas.create_text(
                    (x0 + x1) / 2, h / 2,
                    text=text,
                    anchor="center",
                    font=("Segoe UI", 9, "bold"),
                    fill="#000000"
                )

            # línea inferior del grupo
            self.header_canvas.create_line(
                x0, h - 1, x1, h - 1,
                fill=group_separator_color,
                width=1
            )
            group_boundaries.add(x0)
            group_boundaries.add(x1)

        # líneas finas por cada columna
        for _, x1 in xs:
            self.header_canvas.create_line(x1, 0, x1, h, fill=fine_line_color)

        # remarcar bordes de grupo
        for x_boundary in sorted(list(group_boundaries)):
            if x_boundary == 0:
                continue
            self.header_canvas.create_line(
                x_boundary, 0, x_boundary, h,
                fill=group_separator_color,
                width=1
            )

        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), h))

    def _is_client_column(self, col_id: str) -> bool:
            """
            Devuelve True solo si la columna es del tipo dinámico de cliente:
            ejemplo: c5_estado, c12_hora_llegada, etc.

            Regla:
            - empieza con 'c'
            - después de la 'c' viene un número (id de cliente)
            - luego un '_' y el nombre del campo
            """
            if not col_id.startswith("c"):
                return False

            parts = col_id.split("_", 1)
            if len(parts) != 2:
                return False

            prefix = parts[0]  # ej. 'c5' o 'c12'
            if len(prefix) < 2:
                return False

            # lo que viene después de la 'c' tienen que ser dígitos
            return prefix[1:].isdigit()

    def _insert_initialization_row(self):
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
            # al inicio todo está en cero
            "est_b1_libre": fmt(0),
            "est_b2_libre": fmt(0),
            "est_bib_ocioso_acum": fmt(0),
            "est_cli_perm_acum": fmt(0),
        }

        vals = []
        for col_id in self.tree["columns"]:
            if self._is_client_column(col_id):
                # columnas dinámicas de "Cliente N"
                vals.append("")
            elif col_id == "iteracion":
                vals.append("0")
            else:
                vals.append(str(base.get(col_id, "")))
        self.tree.insert("", "end", values=vals, tags=('evenrow',))

    def _ensure_client_columns(self, cid: int):
        """
        Si aparece un cliente nuevo (por ejemplo Cliente 5),
        agregamos al final 4 columnas:
          c5_estado, c5_hora_llegada, c5_a_que_fue, c5_cuando_termina
        y creamos un grupo "Cliente 5" para el header gráfico.
        """
        if cid in self.known_clients:
            return  # El cliente ya existe, no hacemos nada

        # 1. Actualizar la definición de columnas en memoria
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

        # 2. Registramos este bloque como nuevo grupo visual "Cliente X"
        self.groups.append((f"Cliente {cid}", start_idx, end_idx))

        # 3. Re-aplicamos TODAS las columnas (viejas + nuevas) al Treeview
        #    Esto leerá de self.columns y configurará todo de nuevo.
        self._apply_columns()

        # 4. Redibujamos el header de grupos (que _apply_columns no hace)
        self._draw_group_headers()

    def on_next(self):
        """
        Botón "Siguiente evento":
        - Le pide al motor el siguiente evento.
        - Actualiza columnas de clientes si aparecen nuevos.
        - Inserta la nueva fila.
        - Redibuja SIEMPRE el header de grupos para que nunca desaparezca.
        """
        try:
            if not self.engine.hay_mas():
                stats = self.engine.finalizar_estadisticas()
                self.open_stats()
                self._refresh_stats_window(final=True)
                messagebox.showinfo(
                    "Fin de simulación",
                    "No hay más eventos (límite de tiempo o iteraciones alcanzado)."
                )
                self._draw_group_headers()
                return

            row, cli_snap = self.engine.siguiente_evento()

        except StopIteration as e:
            self.open_stats()
            self._refresh_stats_window(final=True)
            messagebox.showinfo("Fin de simulación", str(e))
            self._draw_group_headers()
            return

        # Creamos columnas por cada cliente activo (incluye los que acaban de destruirse en ESTA fila)
        for cid in sorted(cli_snap.keys()):
            self._ensure_client_columns(cid)

        # Preparamos los valores para TODAS las columnas actuales
        values = []
        for col_id in self.tree["columns"]:
            if self._is_client_column(col_id):
                # --- columnas dinámicas tipo c{id}_campo ---
                try:
                    prefix, campo = col_id.split("_", 1)
                except ValueError:
                    prefix, campo = col_id, ""
                cid_str = prefix[1:] if prefix.startswith("c") else prefix
                try:
                    cid_int = int(cid_str)
                except ValueError:
                    cid_int = None

                if cid_int is not None and cid_int in cli_snap:
                    cli_info = cli_snap[cid_int]
                    if campo == "estado":
                        values.append(cli_info.get("estado", ""))
                    elif campo == "hora_llegada":
                        values.append(cli_info.get("hora_llegada", ""))
                    elif campo == "a_que_fue":
                        values.append(cli_info.get("a_que_fue", ""))
                    elif campo == "cuando_termina":
                        values.append(cli_info.get("cuando_termina", ""))
                    else:
                        values.append("")
                else:
                    values.append("")
            else:
                # --- columnas fijas normales (incluye cola) ---
                if col_id == "iteracion":
                    values.append(str(self.engine.iteration))
                else:
                    v = row.get(col_id, "")
                    values.append("" if v == "" else str(v))

        tag = 'evenrow' if self.engine.iteration % 2 == 0 else 'oddrow'
        self.tree.insert("", "end", values=values, tags=(tag,))

        # Redibujamos SIEMPRE el encabezado de grupos arriba
        self._draw_group_headers()

        # refrescamos ventana de stats si está abierta
        self._refresh_stats_window(final=False)


# ----------------- Ventana Principal (input y validación) -----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 680)

        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.style.map(
            "Treeview",
            background=[("selected", ROW_SELECTED_BG)],
            foreground=[("selected", ROW_SELECTED_FG)],
        )
        self.style.configure("Treeview.Heading",
        background=TREE_HEADER_BG,
        foreground=TREE_HEADER_FG,
        relief="solid",            # Dibuja el borde
        borderwidth=1)
        self.style.configure("Invalid.TEntry", fieldbackground="#ffe6e6")
        self.style.configure("Ok.TLabel", foreground="#15803d")
        self.style.configure("Bad.TLabel", foreground="#dc2626")

        # --- INICIO: MODIFICACIÓN PARA SCROLLBAR ---

        # 1. Hacemos que la fila y columna principal de la ventana (self) se expandan
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # 2. Frame principal que contendrá el canvas y el scrollbar
        main_frame = ttk.Frame(self)
        main_frame.grid(row=0, column=0, sticky="nsew")

        # 3. Hacemos que la fila 0 y col 0 de main_frame se expandan
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # 4. Canvas y Scrollbar
        self.canvas = tk.Canvas(main_frame)
        self.scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # 5. Posicionamos con grid
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        # 6. El frame INTERNO ('root') que contendrá los widgets
        #    Le damos padding aquí en lugar de al main_frame
        root = ttk.Frame(self.canvas, padding=12)

        # 7. Creamos la "ventana" del canvas
        self.canvas_window = self.canvas.create_window((0, 0), window=root, anchor="nw")

        # Hacemos que la columna 0 de 'root' se expanda (para los LabelFrames)
        root.columnconfigure(0, weight=1)

        # Bindings
        root.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind_all("<Button-4>", self.on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self.on_mousewheel_linux)

        # --- FIN: MODIFICACIÓN PARA SCROLLBAR ---

        # El 'root' original ahora es el frame scrolleable
        # El resto del código no necesita cambios, ya que usa 'root'

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
        # Checkbox: ejecutar automáticamente toda la simulación
        self.auto_var = tk.BooleanVar(value=True)  # True = auto por defecto (cámbialo si querés)
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

        self._mk_int(cons, "uni_a", "A (min)", 2, 0, 10_000, "Debe cumplirse A < B y A ≠ B.")
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
        # root.rowconfigure(5, weight=1) # No es necesario en un frame scrolleable
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

    # ---- helpers de UI principal ----
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

    # --- INICIO: MÉTODOS AÑADIDOS PARA SCROLLBAR ---

    def on_frame_configure(self, event=None):
        """Actualiza el scrollregion del canvas."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event=None):
        """Asegura que el frame interno llene el ancho del canvas."""
        canvas_width = event.width
        self.canvas.itemconfig(self.canvas_window, width=canvas_width)

    def on_mousewheel(self, event):
        """Maneja el scroll con la rueda del mouse (Windows/macOS)."""
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def on_mousewheel_linux(self, event):
        """Maneja el scroll con la rueda del mouse (Linux)."""
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")

    # --- FIN: MÉTODOS AÑADIDOS PARA SCROLLBAR ---

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
        # limpiamos estilos rojos
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

        # Armamos el dict final de configuración
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

        # Mostrar config en el textbox y copiar al portapapeles
        self.txt_out.delete("1.0", "end")
        pretty = json.dumps(cfg, indent=2, ensure_ascii=False)
        self.txt_out.insert("1.0", pretty)
        self.clipboard_clear()
        self.clipboard_append(pretty)

        # Abrir la ventana de simulación con esta config
        SimulationWindow(self, cfg)


if __name__ == "__main__":
    App().mainloop()
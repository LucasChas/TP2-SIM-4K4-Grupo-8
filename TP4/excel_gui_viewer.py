# sim_biblioteca_gui.py
import tkinter as tk
from tkinter import ttk, messagebox
import json, random, math

APP_TITLE = "Parámetros de Simulación - Biblioteca (Una sola pantalla)"
GROUP_BG = "#e8efff"
GROUP_BORDER = "#a8b3d7"

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
        self.estado = "EN COLA"          # EN COLA | SIENDO ATENDIDO(i) | EC LEYENDO | DESTRUCCION
        self.hora_llegada = hora_llegada
        self.a_que_fue_inicial = ""      # Pedir | Devolver | Consultar (se fija al entrar en servicio)
        self.accion_actual = ""          # acción atendida ahora (puede ser Devolver al volver de leer)
        self.cuando_termina_leer = ""    # string p/mostrar
        self.fin_lect_num = None         # float para cálculos
        self.hora_entrada_cola = None    # cuando entra/retorna a cola

class Bibliotecario:
    def __init__(self):
        self.estado = "LIBRE"        # LIBRE | OCUPADO
        self.rnd = ""                # solo se muestra en la fila donde se calculó
        self.demora = ""             # idem
        self.hora = ""               # fin programado (string) — se arrastra
        self.hora_num = None         # fin programado (float)
        self.cliente_id = None

# ----------------- Motor de simulación -----------------
class SimulationEngine:
    """
    Avance por 'siguiente evento':
      - LLEGADA_CLIENTE
      - FIN_ATENCION_i (i=1,2)
      - FIN_LECTURA
    Empates: FIN_AT1, FIN_AT2, FIN_LECTURA(ID menor), LLEGADA.
    No se guarda historial de filas: sólo la fila actual y el arrastre necesario.
    """
    def __init__(self, cfg):
        self.cfg = cfg

        # parámetros
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

        # estado global
        self.clock = cfg["simulacion"]["mostrar_vector_estado"]["desde_minuto_j"]
        self.last_clock = self.clock           # para integrar estadísticas
        self.next_arrival = self.clock + self.t_inter
        self.iteration = 0
        self.next_client_id = 1

        self.cola = []                         # ids FIFO
        self.cola_display = 0                  # ARRASTRE: se muestra el valor de la iteración previa
        self.clientes = {}                     # id -> Cliente
        self.bib = [Bibliotecario(), Bibliotecario()]

        # snapshots por cliente (para pintar columnas "CLIENTE N")
        self.snapshots = {}                    # id -> {"estado","hora_llegada","a_que_fue","cuando_termina"}

        # "solo esta fila" (mostrar RND/DEMORA/TRX de quien comienza justo ahora)
        self.last_b = {
            1: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""},
            2: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""}
        }

        # estadísticas acumuladas
        self.est_b1_libre = 0.0
        self.est_b2_libre = 0.0
        self.est_bib_ocioso_acum = 0.0
        self.est_cli_perm_acum = 0.0

        # lectores en biblioteca (contador eficiente)
        self.biblio_personas_cnt = 0
        self.biblio_estado = ""  # placeholder

    # ---------- utilidades ----------
    def _elige_transaccion(self, r):
        if r < self.p_pedir:
            return "Pedir"
        elif r < self.p_pedir + self.p_devolver:
            return "Devolver"
        else:
            return "Consultar"

    def _demora_por_transaccion(self, tipo):
        r = random.random()
        if tipo == "Consultar":
            demora = self.uni_a + (self.uni_b - self.uni_a) * r
        elif tipo == "Devolver":
            demora = 1.5 + r * (2.5 - 1.5)
        else:  # Pedir
            demora = -6.0 * math.log(1.0 - r)
        return r, demora

    def _hay_cola(self):
        return len(self.cola) > 0

    def _primer_bib_libre(self):
        if self.bib[0].estado == "LIBRE": return 0
        if self.bib[1].estado == "LIBRE": return 1
        return None

    def _sortear_transaccion_si_falta(self, c: Cliente):
        """Define a_que_fue_inicial/accion_actual si aún no estaban asignadas; devuelve (trx_rnd, trx_tipo)."""
        if not c.a_que_fue_inicial:
            r_trx = random.random()
            tipo = self._elige_transaccion(r_trx)
            c.a_que_fue_inicial = tipo
            c.accion_actual = tipo
            return fmt(r_trx, 4), tipo
        return "", c.a_que_fue_inicial

    def _tomar_de_cola(self, idx_bib):
        """
        Si hay cola, comienza a atender al primero en self.clock.
        Devuelve (asigno, rnd_serv, demora, trx_rnd, trx_tipo).
        """
        if not self.cola:
            return False, "", "", "", ""
        b = self.bib[idx_bib]
        cid = self.cola.pop(0)
        c = self.clientes[cid]

        # entrar a servicio => sortear transacción si faltaba
        trx_rnd, trx_tipo = self._sortear_transaccion_si_falta(c)

        c.estado = f"SIENDO ATENDIDO({idx_bib+1})"
        self._set_snapshot(cid, c)  # persiste cambio

        r_srv, demora = self._demora_por_transaccion(c.accion_actual)
        b.estado = "OCUPADO"
        b.rnd = fmt(r_srv); b.demora = fmt(demora)
        b.hora_num = self.clock + demora; b.hora = fmt(b.hora_num)
        b.cliente_id = cid
        return True, b.rnd, b.demora, trx_rnd, trx_tipo

    def _set_snapshot(self, cid, c):
        # No sobreescribir si ya quedó en DESTRUCCION
        prev = self.snapshots.get(cid)
        if prev and prev.get("estado") == "DESTRUCCION":
            return
        self.snapshots[cid] = {
            "estado": c.estado,
            "hora_llegada": fmt(c.hora_llegada, 2),
            "a_que_fue": c.a_que_fue_inicial,
            "cuando_termina": c.cuando_termina_leer
        }

    # ---------- estadísticas (integración entre eventos) ----------
    def _integrar_estadisticas_hasta(self, new_time: float):
        dt = new_time - self.last_clock
        if dt <= 0:
            return
        # tiempos libres de bibliotecarios
        if self.bib[0].estado == "LIBRE":
            self.est_b1_libre += dt
        if self.bib[1].estado == "LIBRE":
            self.est_b2_libre += dt
        self.est_bib_ocioso_acum = self.est_b1_libre + self.est_b2_libre

        # N(t): en sistema = cola + en servicio + leyendo
        en_servicio = (1 if self.bib[0].estado == "OCUPADO" else 0) + (1 if self.bib[1].estado == "OCUPADO" else 0)
        n_sistema = en_servicio + self.biblio_personas_cnt + len(self.cola)
        self.est_cli_perm_acum += n_sistema * dt

        self.last_clock = new_time

    # ---------- selección del próximo evento ----------
    def _proximo_evento(self):
        cand = []
        if self.bib[0].hora_num is not None:
            cand.append((self.bib[0].hora_num, 1, "fin_atencion", {"i":1}))
        if self.bib[1].hora_num is not None:
            cand.append((self.bib[1].hora_num, 2, "fin_atencion", {"i":2}))
        for cid, c in self.clientes.items():
            if c.estado == "EC LEYENDO" and c.fin_lect_num is not None:
                cand.append((c.fin_lect_num, 3 + cid*1e-6, "fin_lectura", {"cid": cid}))
        if self.next_arrival is not None:
            cand.append((self.next_arrival, 4, "llegada", {}))
        if not cand:
            return None
        return min(cand, key=lambda x: (x[0], x[1]))

    def hay_mas(self):
        ne = self._proximo_evento()
        if ne is None: return False
        t, *_ = ne
        return self.iteration < self.iter_limit and t <= self.time_limit

    # ---------- eventos ----------
    def _evento_llegada(self):
        t = self.next_arrival
        self._integrar_estadisticas_hasta(t)

        self.iteration += 1
        self.clock = t
        # reset "solo esta fila"
        self.last_b = {1: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""},
                       2: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""}}

        cid = self.next_client_id; self.next_client_id += 1
        c = Cliente(cid, hora_llegada=self.clock)

        # al llegar: NO decidir transacción aún
        asignado = None
        if not self._hay_cola():
            libre = self._primer_bib_libre()
            if libre is not None: asignado = libre

        if asignado is None:
            c.estado = "EN COLA"; c.hora_entrada_cola = self.clock; self.cola.append(c.id)
            trx_rnd, trx_tipo = "", ""  # no hay transacción todavía
        else:
            # entra directo a servicio → sortear transacción aquí
            trx_rnd, trx_tipo = self._sortear_transaccion_si_falta(c)
            c.estado = f"SIENDO ATENDIDO({asignado+1})"
            r_srv, demora = self._demora_por_transaccion(c.accion_actual)
            b = self.bib[asignado]
            b.estado = "OCUPADO"
            b.rnd = fmt(r_srv); b.demora = fmt(demora)
            b.hora_num = self.clock + demora; b.hora = fmt(b.hora_num)
            b.cliente_id = c.id
            # para mostrar en esta fila
            self.last_b[asignado+1]["rnd"] = b.rnd
            self.last_b[asignado+1]["demora"] = b.demora
            self.last_b[asignado+1]["trx_rnd"] = trx_rnd
            self.last_b[asignado+1]["trx_tipo"] = trx_tipo

        self.clientes[c.id] = c
        self._set_snapshot(c.id, c)

        # próxima llegada (solo en llegada)
        self.next_arrival = self.clock + self.t_inter

        # fila (COLA arrastrada del estado previo)
        row = {
            "evento": f"LLEGADA_CLIENTE({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": fmt(self.t_inter, 2),       # SOLO en llegada
            "lleg_minuto": fmt(self.next_arrival, 2),  # se arrastra hacia adelante
            "lleg_id": str(cid),
            "trx_rnd": trx_rnd,
            "trx_tipo": trx_tipo,
            "lee_rnd": "", "lee_lugar": "", "lee_tiempo": "", "lee_fin": "",
            "b1_estado": self.bib[0].estado, "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.last_b[1]["demora"], "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado, "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.last_b[2]["demora"], "b2_hora": self.bib[1].hora,
            "cola": self.cola_display,  # <-- ARRASTRE
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self.biblio_personas_cnt,
            "est_b1_libre": fmt(self.est_b1_libre),
            "est_b2_libre": fmt(self.est_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.est_cli_perm_acum),
        }

        # actualizar arrastre para la próxima fila
        self.cola_display = len(self.cola)
        return row

    def _evento_fin_atencion(self, i):
        idx = i - 1
        b = self.bib[idx]
        t = b.hora_num
        self._integrar_estadisticas_hasta(t)

        self.iteration += 1
        self.clock = t
        self.last_b = {1: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""},
                       2: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""}}

        cid = b.cliente_id
        c = self.clientes[cid]

        # Decide destino
        lee_rnd = ""; lee_lugar = ""; lee_tiempo = ""; lee_fin = ""
        if c.a_que_fue_inicial == "Pedir":
            r = random.random(); lee_rnd = fmt(r, 4)
            if r < self.p_retira:
                c.estado = "DESTRUCCION"
                c.fin_lect_num = None; c.cuando_termina_leer = ""
            else:
                c.estado = "EC LEYENDO"
                fin_lec = self.clock + self.t_lect_biblio
                c.fin_lect_num = fin_lec
                c.cuando_termina_leer = fmt(fin_lec, 2)
                lee_lugar = "Biblioteca"; lee_tiempo = fmt(self.t_lect_biblio, 2); lee_fin = c.cuando_termina_leer
                self.biblio_personas_cnt += 1  # entra a leer en sala
        else:
            c.estado = "DESTRUCCION"
            c.fin_lect_num = None; c.cuando_termina_leer = ""

        self._set_snapshot(cid, c)

        # liberar b y tomar de cola (si hay, aquí se fija transacción del nuevo que entra)
        b.estado = "LIBRE"; b.rnd=""; b.demora=""; b.hora=""; b.hora_num=None; b.cliente_id=None
        asigno, rnd, demora, trx_rnd, trx_tipo = self._tomar_de_cola(idx)
        if asigno:
            self.last_b[i]["rnd"] = rnd
            self.last_b[i]["demora"] = demora
            self.last_b[i]["trx_rnd"] = trx_rnd
            self.last_b[i]["trx_tipo"] = trx_tipo

        row = {
            "evento": f"FIN_ATENCION_{i}({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": "",
            "lleg_minuto": fmt(self.next_arrival,2),
            "lleg_id": "",
            "trx_rnd": self.last_b[i]["trx_rnd"],   # si se tomó a alguien de la cola
            "trx_tipo": self.last_b[i]["trx_tipo"],
            "lee_rnd": lee_rnd, "lee_lugar": lee_lugar, "lee_tiempo": lee_tiempo, "lee_fin": lee_fin,
            "b1_estado": self.bib[0].estado, "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.last_b[1]["demora"], "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado, "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.last_b[2]["demora"], "b2_hora": self.bib[1].hora,
            "cola": self.cola_display,  # <-- ARRASTRE
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self.biblio_personas_cnt,
            "est_b1_libre": fmt(self.est_b1_libre),
            "est_b2_libre": fmt(self.est_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.est_cli_perm_acum),
        }

        # actualizar arrastre para la próxima fila
        self.cola_display = len(self.cola)
        return row

    def _evento_fin_lectura(self, cid):
        c = self.clientes[cid]
        t = c.fin_lect_num
        self._integrar_estadisticas_hasta(t)

        self.iteration += 1
        self.clock = t
        self.last_b = {1: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""},
                       2: {"rnd":"", "demora":"", "trx_rnd":"", "trx_tipo":""}}

        # Vuelve con "Devolver"
        c.fin_lect_num = None
        c.cuando_termina_leer = ""
        c.accion_actual = "Devolver"
        self.biblio_personas_cnt = max(0, self.biblio_personas_cnt - 1)  # deja de leer en sala

        libre = self._primer_bib_libre()
        if libre is not None:
            c.estado = f"SIENDO ATENDIDO({libre+1})"
            r_srv, demora = self._demora_por_transaccion(c.accion_actual)
            b = self.bib[libre]
            b.estado = "OCUPADO"; b.rnd = fmt(r_srv); b.demora = fmt(demora)
            b.hora_num = self.clock + demora; b.hora = fmt(b.hora_num); b.cliente_id = c.id
            self.last_b[libre+1]["rnd"] = b.rnd; self.last_b[libre+1]["demora"] = b.demora
            self.last_b[libre+1]["trx_rnd"] = ""   # no corresponde sorteo aquí
            self.last_b[libre+1]["trx_tipo"] = "Devolver"
        else:
            c.estado = "EN COLA"; c.hora_entrada_cola = self.clock; self.cola.append(c.id)

        self._set_snapshot(c.id, c)

        row = {
            "evento": f"FIN_LECTURA({cid})",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": "", "lleg_minuto": fmt(self.next_arrival,2), "lleg_id": "",
            "trx_rnd": "" if libre is None else self.last_b[libre+1]["trx_rnd"],
            "trx_tipo": "" if libre is None else self.last_b[libre+1]["trx_tipo"],
            "lee_rnd": "", "lee_lugar": "", "lee_tiempo": "", "lee_fin": "",
            "b1_estado": self.bib[0].estado, "b1_rnd": self.last_b[1]["rnd"],
            "b1_demora": self.last_b[1]["demora"], "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado, "b2_rnd": self.last_b[2]["rnd"],
            "b2_demora": self.last_b[2]["demora"], "b2_hora": self.bib[1].hora,
            "cola": self.cola_display,  # <-- ARRASTRE
            "biblio_estado": self.biblio_estado,
            "biblio_personas": self.biblio_personas_cnt,
            "est_b1_libre": fmt(self.est_b1_libre),
            "est_b2_libre": fmt(self.est_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.est_cli_perm_acum),
        }

        # actualizar arrastre para la próxima fila
        self.cola_display = len(self.cola)
        return row

    # ---------- paso general ----------
    def siguiente_evento(self):
        ne = self._proximo_evento()
        if ne is None: raise StopIteration("No hay eventos pendientes.")
        t, _, tipo, data = ne
        if self.iteration >= self.iter_limit: raise StopIteration("Máximo de iteraciones alcanzado.")
        if t > self.time_limit: raise StopIteration("Se alcanzó el tiempo límite X.")

        if tipo == "llegada":
            return self._evento_llegada()
        elif tipo == "fin_atencion":
            return self._evento_fin_atencion(data["i"])
        else:
            return self._evento_fin_lectura(data["cid"])

    # ---------- acceso para UI ----------
    def get_snapshot_map(self):
        out = {}
        for cid, snap in self.snapshots.items():
            out[f"c{cid}_estado"] = snap["estado"]
            out[f"c{cid}_hora_llegada"] = snap["hora_llegada"]
            out[f"c{cid}_a_que_fue"] = snap["a_que_fue"]
            out[f"c{cid}_cuando_termina"] = snap["cuando_termina"]
        return out

    def get_known_client_ids(self):
        return sorted(self.snapshots.keys())

# ----------------- 2ª Ventana (Vector de Estado) -----------------
class SimulationWindow(tk.Toplevel):
    def __init__(self, master, config_dict):
        super().__init__(master)
        self.title("Vector de Estado - Simulación")
        self.geometry("1400x760")
        self.minsize(1200, 560)

        self.engine = SimulationEngine(config_dict)

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # Barra superior
        top = ttk.Frame(root); top.pack(fill="x")
        resumen = ttk.Label(
            top,
            text=(f"Config → X={config_dict['simulacion']['tiempo_limite_min']} min | "
                  f"N={config_dict['simulacion']['iteraciones_max']} | "
                  f"i={config_dict['simulacion']['mostrar_vector_estado']['i_iteraciones']} "
                  f"desde j={config_dict['simulacion']['mostrar_vector_estado']['desde_minuto_j']}  "
                  f"| t_entre_llegadas={config_dict['llegadas']['tiempo_entre_llegadas_min']} min"),
            foreground="#374151"
        ); resumen.pack(side="left")
        ttk.Button(top, text="Siguiente evento", command=self.on_next).pack(side="right")

        # ------ columnas fijas ------
        self.columns = []
        def add_col(cid, text, w): self.columns.append({"id": cid, "text": text, "w": w})

        add_col("iteracion", "Numero de iteracion", 160)
        add_col("evento", "Evento", 180)
        add_col("reloj", "Reloj (minutos)", 130)
        # LLEGADA_CLIENTE
        add_col("lleg_tiempo", "TIEMPO", 90)              # solo en filas de llegada
        add_col("lleg_minuto", "MINUTO QUE LLEGA", 165)   # se arrastra
        add_col("lleg_id", "ID Cliente", 110)
        # TRANSACCION
        add_col("trx_rnd", "RND", 80)
        add_col("trx_tipo", "Tipo Transaccion", 160)
        # ¿Dónde Lee?
        add_col("lee_rnd", "RND", 70)
        add_col("lee_lugar", "LUGAR", 110)
        add_col("lee_tiempo", "TIEMPO", 100)
        add_col("lee_fin", "Fin Lectura", 130)
        # BIBLIO 1
        add_col("b1_estado", "Estado", 90)
        add_col("b1_rnd", "RND", 70)
        add_col("b1_demora", "Demora", 100)
        add_col("b1_hora", "Hora", 110)
        # BIBLIO 2
        add_col("b2_estado", "Estado", 90)
        add_col("b2_rnd", "RND", 70)
        add_col("b2_demora", "Demora", 100)
        add_col("b2_hora", "Hora", 110)
        # COLA
        add_col("cola", "COLA", 90)
        # BIBLIOTECA
        add_col("biblio_estado", "Estado", 95)
        add_col("biblio_personas", "Personas en la biblioteca (MAXIMO 20)", 270)
        # ESTADISTICAS
        add_col("est_b1_libre", "TIEMPO LIBRE BIBLIOTECARIO1", 230)
        add_col("est_b2_libre", "TIEMPO LIBRE BIBLIOTECARIO2", 230)
        add_col("est_bib_ocioso_acum", "ACUMULADOR TIEMPO OCIOSO BIBLIOTECARIOS", 330)
        add_col("est_cli_perm_acum", "ACUMULADOR TIEMPO PERMANENCIA", 270)

        # Grupos superiores
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

        # Dinámicos por cliente
        self.known_client_ids = []

        # header + tree
        wrapper = ttk.Frame(root); wrapper.pack(fill="both", expand=True)
        self.header_canvas = tk.Canvas(wrapper, height=28, background="#ffffff", highlightthickness=0)
        self.header_canvas.pack(fill="x", side="top")
        self.tree = ttk.Treeview(wrapper, show="headings", height=20)
        self.tree.pack(fill="both", expand=True, side="left")
        yscroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview); yscroll.pack(fill="y", side="right")
        xscroll = ttk.Scrollbar(root, orient="horizontal"); xscroll.pack(fill="x", side="bottom")

        def on_xscroll(*args):
            self.tree.xview(*args); self.header_canvas.xview(*args)
        def on_tree_xscroll(lo, hi):
            xscroll.set(lo, hi); self.header_canvas.xview_moveto(lo)

        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=on_tree_xscroll)
        xscroll.configure(command=on_xscroll)

        self._apply_columns()
        self._draw_group_headers()
        self._insert_initialization_row()

    def _apply_columns(self):
        self.col_ids = [c["id"] for c in self.columns]
        self.tree["columns"] = self.col_ids
        for c in self.columns:
            self.tree.heading(c["id"], text=c["text"], anchor="center")
            self.tree.column(c["id"], width=c["w"], minwidth=40, anchor="center", stretch=False)
        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), 28))

    def _add_client_columns(self, cid):
        start_idx = len(self.columns)
        self.columns += [
            {"id": f"c{cid}_estado", "text": "ESTADO",  "w": 110},
            {"id": f"c{cid}_hora_llegada", "text": "HORA_LLEGADA", "w": 130},
            {"id": f"c{cid}_a_que_fue", "text": "A QUE FUE", "w": 120},
            {"id": f"c{cid}_cuando_termina", "text": "Cuando termina de leer", "w": 180},
        ]
        end_idx = len(self.columns) - 1
        self.groups.append((f"CLIENTE {cid}", start_idx, end_idx))
        self._apply_columns()
        self._draw_group_headers()
        for item in self.tree.get_children(""):
            vals = list(self.tree.item(item, "values"))
            vals.extend(["", "", "", ""])
            self.tree.item(item, values=vals)

    def _total_width(self):
        return sum(self.tree.column(c["id"], option="width") for c in self.columns)

    def _col_x_positions(self):
        xs, acc = [], 0
        for c in self.columns:
            w = self.tree.column(c["id"], option="width"); xs.append((acc, acc + w)); acc += w
        return xs

    def _draw_group_headers(self):
        self.header_canvas.delete("all")
        xs = self._col_x_positions(); h = 28
        for text, i0, i1 in self.groups:
            x0 = xs[i0][0]; x1 = xs[i1][1]
            self.header_canvas.create_rectangle(x0, 0, x1, h, fill=GROUP_BG, outline=GROUP_BORDER)
            self.header_canvas.create_text((x0 + x1) / 2, h / 2, text=text, anchor="center", font=("Segoe UI", 9, "bold"))
        for x0, x1 in xs:
            self.header_canvas.create_line(x1, 0, x1, h, fill="#e5e7eb")
        self.bind("<Configure>", lambda e: self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), h)))

    def _insert_initialization_row(self):
        eng = self.engine
        base = {
            "iteracion": 0, "evento": "INICIALIZACION", "reloj": fmt(eng.clock,2),
            "lleg_tiempo": "", "lleg_minuto": fmt(eng.next_arrival,2), "lleg_id": "",
            "trx_rnd": "", "trx_tipo": "",
            "lee_rnd": "", "lee_lugar": "", "lee_tiempo": "", "lee_fin": "",
            "b1_estado": "LIBRE", "b1_rnd": "", "b1_demora": "", "b1_hora": "",
            "b2_estado": "LIBRE", "b2_rnd": "", "b2_demora": "", "b2_hora": "",
            "cola": self.engine.cola_display,  # arrastre inicial = 0
            "biblio_estado": "", "biblio_personas": 0,
            "est_b1_libre": fmt(0), "est_b2_libre": fmt(0),
            "est_bib_ocioso_acum": fmt(0), "est_cli_perm_acum": fmt(0),
        }
        vals = [base.get(cid, "") for cid in [c["id"] for c in self.columns]]
        self.tree.insert("", "end", values=vals)

    def on_next(self):
        try:
            if not self.engine.hay_mas():
                messagebox.showinfo("Simulación", "No hay más eventos (límite de tiempo o iteraciones alcanzado).")
                return
            row = self.engine.siguiente_evento()
        except StopIteration as e:
            messagebox.showinfo("Simulación", str(e)); return

        # agregar columnas para nuevos clientes si aparecieron
        now_ids = self.engine.get_known_client_ids()
        for cid in now_ids:
            if cid not in self.known_client_ids:
                self.known_client_ids.append(cid)
                self._add_client_columns(cid)

        snap = self.engine.get_snapshot_map()

        # construimos values "al vuelo"; no guardamos historial en listas
        values = []
        for col in self.col_ids:
            if col.startswith("c"):
                values.append(snap.get(col, ""))
            else:
                if col == "iteracion":
                    values.append(str(self.engine.iteration))
                else:
                    values.append(row.get(col, ""))
        self.tree.insert("", "end", values=values)

# ----------------- 1ª Ventana (config) -----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 680)

        self.style = ttk.Style(self)
        self.style.configure("Invalid.TEntry", fieldbackground="#ffe6e6")
        self.style.configure("Ok.TLabel", foreground="#15803d")
        self.style.configure("Bad.TLabel", foreground="#dc2626")
        self.columnconfigure(0, weight=1)

        root = ttk.Frame(self, padding=12); root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        self.fields = {}

        sim = ttk.LabelFrame(root, text="1) Simulación (todo en minutos)")
        sim.grid(row=0, column=0, sticky="ew", pady=(0, 8)); sim.columnconfigure(1, weight=1)
        self._mk_int(sim, "tiempo_limite", "Tiempo límite X", 60, 1, 10_000,
                     "La simulación termina al llegar a X o a N iteraciones (lo que ocurra primero).")
        self._mk_int(sim, "iteraciones_max", "Cantidad de iteraciones N", 1000, 1, 100_000,
                     "Máximo permitido: 100000.")
        self._mk_int(sim, "i_mostrar", "i (iteraciones a mostrar)", 10, 1, 100_000,
                     "Cuántas iteraciones del vector de estado se listarán.")
        self._mk_int(sim, "j_inicio", "j (minuto de inicio)", 0, 0, 10_000,
                     "Minuto desde el cual se comienzan a mostrar las i iteraciones.")

        lleg = ttk.LabelFrame(root, text="2) Llegadas")
        lleg.grid(row=1, column=0, sticky="ew", pady=(0, 8)); lleg.columnconfigure(1, weight=1)
        self._mk_int(lleg, "t_entre_llegadas", "Tiempo entre llegadas (min)", 4, 1, 10_000,
                     "Entero en minutos (por defecto 4).")

        motivos = ttk.LabelFrame(root, text="3) Motivos de llegada (%) — Debe sumar 100%")
        motivos.grid(row=2, column=0, sticky="ew", pady=(0, 8)); motivos.columnconfigure(1, weight=1)
        self._mk_int(motivos, "pct_pedir", "Pedir libros (%)", 45, 0, 100, on_change=self._update_pct_sum)
        self._mk_int(motivos, "pct_devolver", "Devolver libros (%)", 45, 0, 100, on_change=self._update_pct_sum)
        self._mk_int(motivos, "pct_consultar", "Consultar hacerse socio (%)", 10, 0, 100, on_change=self._update_pct_sum)
        sumrow = ttk.Frame(motivos); sumrow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(sumrow, text="Suma actual:").pack(side="left")
        self.lbl_sum = ttk.Label(sumrow, text="0%", style="Bad.TLabel"); self.lbl_sum.pack(side="left", padx=6)

        cons = ttk.LabelFrame(root, text="4) Consultas — Distribución Uniforme(A, B) en minutos")
        cons.grid(row=3, column=0, sticky="ew", pady=(0, 8)); cons.columnconfigure(1, weight=1)
        self._mk_int(cons, "uni_a", "A (min)", 2, 0, 10_000, "Debe cumplirse A < B y A ≠ B.")
        self._mk_int(cons, "uni_b", "B (min)", 5, 0, 10_000)

        lect = ttk.LabelFrame(root, text="5) Lectura")
        lect.grid(row=4, column=0, sticky="ew", pady=(0, 8)); lect.columnconfigure(1, weight=1)
        self._mk_int(lect, "pct_retira", "Se retira a leer en casa (%)", 60, 0, 100, on_change=self._update_queda)
        fila_queda = ttk.Frame(lect); fila_queda.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(fila_queda, text="Se queda a leer en biblioteca (%)").pack(side="left")
        self.lbl_queda = ttk.Label(fila_queda, text="40"); self.lbl_queda.pack(side="left", padx=8)
        self._mk_int(lect, "t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 30, 1, 10_000,
                     "Entero positivo (no 0).")

        salida = ttk.LabelFrame(root, text="6) Resultado")
        salida.grid(row=5, column=0, sticky="nsew", pady=(0, 8))
        root.rowconfigure(5, weight=1); salida.columnconfigure(0, weight=1)
        self.txt_out = tk.Text(salida, height=10); self.txt_out.grid(row=0, column=0, sticky="nsew")
        salida.rowconfigure(0, weight=1)

        btns = ttk.Frame(root); btns.grid(row=6, column=0, sticky="e")
        ttk.Button(btns, text="Restablecer", command=self.reset_defaults).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="Generar", command=self.on_generate).grid(row=0, column=1)

        self.reset_defaults(); self._update_pct_sum(); self._update_queda()

    def _mk_int(self, parent, key, label, default, lo, hi, help_=None, on_change=None):
        row = ttk.Frame(parent)
        r = max((child.grid_info().get("row", -1) for child in parent.winfo_children()
                 if isinstance(child, ttk.Frame)), default=-1) + 1
        row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=3); row.columnconfigure(1, weight=1)
        ttk.Label(row, text=label, width=34, anchor="w").grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=str(default)); ent = ttk.Entry(row, textvariable=var, width=14)
        ent.grid(row=0, column=1, sticky="w", padx=(0, 8))
        if help_: ttk.Label(row, text=help_, foreground="#6b7280").grid(row=0, column=2, sticky="w")
        def only_digits(P): return (P == "") or P.isdigit()
        vcmd = (self.register(only_digits), "%P"); ent.configure(validate="key", validatecommand=vcmd)
        self.fields[key] = {"var": var, "entry": ent, "lo": lo, "hi": hi, "default": default}
        if on_change: var.trace_add("write", lambda *args: on_change())
        return ent

    def _update_pct_sum(self):
        s = 0
        for k in ("pct_pedir", "pct_devolver", "pct_consultar"):
            v = int_or_none(self.fields[k]["var"].get()); s += v if v is not None else 0
        self.lbl_sum.configure(text=f"{s}%"); self.lbl_sum.configure(style="Ok.TLabel" if s == 100 else "Bad.TLabel")

    def _update_queda(self):
        p = int_or_none(self.fields["pct_retira"]["var"].get()); p = 0 if p is None else p
        self.lbl_queda.configure(text=str(max(0, min(100, 100 - p))))

    def reset_defaults(self):
        defaults = {
            "tiempo_limite": 60, "iteraciones_max": 1000, "i_mostrar": 10, "j_inicio": 0,
            "t_entre_llegadas": 4, "pct_pedir": 45, "pct_devolver": 45, "pct_consultar": 10,
            "uni_a": 2, "uni_b": 5, "pct_retira": 60, "t_lectura_biblio": 30
        }
        for k, meta in self.fields.items(): meta["entry"].configure(style="TEntry")
        for k, v in defaults.items(): self.fields[k]["var"].set(str(v))
        self.txt_out.delete("1.0", "end")

    def on_generate(self):
        for meta in self.fields.values(): meta["entry"].configure(style="TEntry")
        errors, mark = [], []
        def need_int(key, desc, lo, hi):
            val = int_or_none(self.fields[key]["var"].get())
            if not between(val, lo, hi): errors.append(f"• {desc}: debe ser entero en [{lo}, {hi}]"); mark.append(key)
            return val

        t_lim = need_int("tiempo_limite", "Tiempo límite X", 1, 10_000)
        n_max = need_int("iteraciones_max", "Cantidad de iteraciones N", 1, 100_000)
        i_mos = need_int("i_mostrar", "i (iteraciones a mostrar)", 1, 100_000)
        j_ini = need_int("j_inicio", "j (minuto de inicio)", 0, 10_000)
        if None not in (t_lim, j_ini) and j_ini >= t_lim: errors.append("• j debe ser menor que X."); mark+=["j_inicio","tiempo_limite"]
        if None not in (i_mos, n_max) and i_mos > n_max: errors.append("• i no debería exceder N."); mark+=["i_mostrar","iteraciones_max"]

        t_lleg = need_int("t_entre_llegadas", "Tiempo entre llegadas (min)", 1, 10_000)
        p_ped = need_int("pct_pedir", "Pedir libros (%)", 0, 100)
        p_dev = need_int("pct_devolver", "Devolver libros (%)", 0, 100)
        p_con = need_int("pct_consultar", "Consultar hacerse socio (%)", 0, 100)
        if None not in (p_ped, p_dev, p_con) and (p_ped + p_dev + p_con != 100):
            errors.append(f"• La suma de motivos debe ser 100% (ahora {p_ped+p_dev+p_con}%).")
            mark+=["pct_pedir","pct_devolver","pct_consultar"]
        a = need_int("uni_a", "Uniforme A (min)", 0, 10_000); b = need_int("uni_b","Uniforme B (min)",0,10_000)
        if None not in (a,b):
            if a == b: errors.append("• En Uniforme(A,B) debe cumplirse A ≠ B."); mark+=["uni_a","uni_b"]
            if a > b: errors.append("• En Uniforme(A,B) debe cumplirse A < B."); mark+=["uni_a","uni_b"]
        p_ret = need_int("pct_retira", "Se retira a leer en casa (%)", 0, 100)
        t_bib = need_int("t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 1, 10_000)

        if errors:
            for k in set(mark): self.fields[k]["entry"].configure(style="Invalid.TEntry")
            messagebox.showerror("Validación", "Revisá:\n\n" + "\n".join(errors)); return

        cfg = {
            "simulacion": {
                "tiempo_limite_min": t_lim,
                "iteraciones_max": n_max,
                "mostrar_vector_estado": {"i_iteraciones": i_mos, "desde_minuto_j": j_ini}
            },
            "llegadas": {"tiempo_entre_llegadas_min": t_lleg},
            "motivos": {
                "pedir_libros_pct": p_ped, "devolver_libros_pct": p_dev, "consultar_socios_pct": p_con
            },
            "consultas_uniforme": {"a_min": a, "b_min": b},
            "lectura": {
                "retira_casa_pct": p_ret, "queda_biblioteca_pct": 100 - p_ret,
                "tiempo_fijo_biblioteca_min": t_bib
            }
        }

        self.txt_out.delete("1.0", "end")
        pretty = json.dumps(cfg, indent=2, ensure_ascii=False)
        self.txt_out.insert("1.0", pretty)
        self.clipboard_clear(); self.clipboard_append(pretty)

        SimulationWindow(self, cfg)

if __name__ == "__main__":
    App().mainloop()

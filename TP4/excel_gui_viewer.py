import tkinter as tk
from tkinter import ttk, messagebox
import json, random, math

APP_TITLE = "Parámetros de Simulación - Biblioteca (Una sola pantalla)"
GROUP_BG = "#e8efff"
GROUP_BORDER = "#a8b3d7"
NUM_CLIENTES = 3  # columnas de CLIENTE 1..N al final

# ----------------- Helpers -----------------
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
        self.estado = "EN COLA"      # EN COLA | SIENDO ATENDIDO(i) | EC LEYENDO | L
        self.hora_llegada = hora_llegada
        self.a_que_fue = ""          # Pedir | Devolver | Consultar
        self.cuando_termina_leer = ""   # string p/mostrar
        self.fin_lect_num = None        # float p/cálculo
        self.hora_entrada_cola = None   # cuando entra a cola (luego de leer)

class Bibliotecario:
    def __init__(self):
        self.estado = "LIBRE"        # LIBRE | OCUPADO
        self.rnd = ""
        self.demora = ""
        self.hora = ""               # fin (string)
        self.hora_num = None         # fin (float)
        self.cliente_id = None

# ----------------- Motor de simulación -----------------
class SimulationEngine:
    """
    Eventos:
      - LLEGADA_CLIENTE
      - FIN_ATENCION_i (i=1,2)
      - FIN_LECTURA
    Selección automática del próximo evento por tiempo mínimo.
    """
    def __init__(self, cfg, max_slots_clientes=NUM_CLIENTES):
        self.cfg = cfg
        self.max_slots = max_slots_clientes

        # tiempos y porcentajes
        self.t_inter = cfg["llegadas"]["tiempo_entre_llegadas_min"]
        self.p_pedir = cfg["motivos"]["pedir_libros_pct"] / 100.0
        self.p_devolver = cfg["motivos"]["devolver_libros_pct"] / 100.0
        self.p_consultar = cfg["motivos"]["consultar_socios_pct"] / 100.0

        self.uni_a = cfg["consultas_uniforme"]["a_min"]
        self.uni_b = cfg["consultas_uniforme"]["b_min"]

        # lectura
        self.p_retira = cfg["lectura"]["retira_casa_pct"] / 100.0
        self.t_lect_biblio = cfg["lectura"]["tiempo_fijo_biblioteca_min"]

        # límites
        self.time_limit = cfg["simulacion"]["tiempo_limite_min"]
        self.iter_limit = cfg["simulacion"]["iteraciones_max"]

        # simulación
        self.clock = cfg["simulacion"]["mostrar_vector_estado"]["desde_minuto_j"]
        self.next_arrival = self.clock + self.t_inter  # 1ª llegada en j + t_inter
        self.iteration = 0
        self.next_client_id = 1

        # estado
        self.cola = []             # ids FIFO
        self.clientes = {}         # id -> Cliente
        self.bib = [Bibliotecario(), Bibliotecario()]

        # métricas / placeholders (a completar más adelante)
        self.biblio_estado = ""
        self.biblio_personas = 0
        self.est_b1_libre = 0.0
        self.est_b2_libre = 0.0
        self.est_bib_ocioso_acum = 0.0
        self.est_cli_perm_acum = 0.0

    # --------- utilidades ----------
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

    def _hay_alguien_en_cola(self):
        return len(self.cola) > 0

    def _primer_bibliotecario_libre(self):
        if self.bib[0].estado == "LIBRE":
            return 0
        if self.bib[1].estado == "LIBRE":
            return 1
        return None

    def _tomar_de_cola(self, idx_bib):
        """Si hay cola, comienza a atender al primero en self.clock."""
        if not self.cola:
            return
        b = self.bib[idx_bib]
        cid = self.cola.pop(0)
        c = self.clientes[cid]
        c.estado = f"SIENDO ATENDIDO({idx_bib+1})"
        r_srv, demora = self._demora_por_transaccion(c.a_que_fue)
        b.estado = "OCUPADO"
        b.rnd = fmt(r_srv)
        b.demora = fmt(demora)
        b.hora_num = self.clock + demora
        b.hora = fmt(b.hora_num)
        b.cliente_id = cid

    # --------- selección del próximo evento ----------
    def _proximo_evento(self):
        """
        Devuelve (t, prioridad, tipo, data)
        tipo ∈ {"llegada", "fin_atencion", "fin_lectura"}
        data: {"i":1/2} o {"cid":...}
        Prioridades para empates: 1=FIN_AT_1, 2=FIN_AT_2, 3=FIN_LECTURA, 4=LLEGADA
        """
        candidatos = []

        # FIN_ATENCION 1 y 2
        if self.bib[0].hora_num is not None:
            candidatos.append((self.bib[0].hora_num, 1, "fin_atencion", {"i": 1}))
        if self.bib[1].hora_num is not None:
            candidatos.append((self.bib[1].hora_num, 2, "fin_atencion", {"i": 2}))

        # FIN_LECTURA (usar ID para desempatar entre lectores)
        for cid, c in self.clientes.items():
            if c.estado == "EC LEYENDO" and c.fin_lect_num is not None:
                # prioridad base 3, pero con micro-desempate por ID
                candidatos.append((c.fin_lect_num, 3 + cid * 1e-6, "fin_lectura", {"cid": cid}))

        # LLEGADA
        if self.next_arrival is not None:
            candidatos.append((self.next_arrival, 4, "llegada", {}))

        if not candidatos:
            return None

        t, pr, tipo, data = min(candidatos, key=lambda x: (x[0], x[1]))
        return (t, pr, tipo, data)

    def hay_mas(self):
        ne = self._proximo_evento()
        if ne is None:
            return False
        t, *_ = ne
        if self.iteration >= self.iter_limit:
            return False
        if t > self.time_limit:
            return False
        return True

    # --------- fila común (rellena columnas compartidas) ----------
    def _armar_fila_base(self, evento):
        row = {
            "iteracion": self.iteration,
            "evento": evento,
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": fmt(self.t_inter, 2),
            "lleg_minuto": fmt(self.next_arrival, 2),
            "trx_rnd": "", "trx_tipo": "",
            "lee_rnd": "", "lee_lugar": "", "lee_tiempo": "", "lee_fin": "",
            "b1_estado": self.bib[0].estado, "b1_rnd": self.bib[0].rnd,
            "b1_demora": self.bib[0].demora, "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado, "b2_rnd": self.bib[1].rnd,
            "b2_demora": self.bib[1].demora, "b2_hora": self.bib[1].hora,
            "cola": len(self.cola),
            "biblio_estado": self.biblio_estado, "biblio_personas": self.biblio_personas,
            "est_b1_libre": fmt(self.est_b1_libre), "est_b2_libre": fmt(self.est_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.est_cli_perm_acum),
        }
        self._llenar_bloque_clientes(row)
        return row

    def _llenar_bloque_clientes(self, row):
        vivos = [self.clientes[k] for k in sorted(self.clientes.keys())
                 if self.clientes[k].estado != "L"]
        vivos = vivos[: self.max_slots]
        for i in range(self.max_slots):
            if i < len(vivos):
                cli = vivos[i]
                row[f"c{i+1}_estado"] = cli.estado
                row[f"c{i+1}_hora_llegada"] = fmt(cli.hora_llegada, 2)
                row[f"c{i+1}_a_que_fue"] = cli.a_que_fue
                row[f"c{i+1}_cuando_termina"] = cli.cuando_termina_leer
            else:
                row[f"c{i+1}_estado"] = ""
                row[f"c{i+1}_hora_llegada"] = ""
                row[f"c{i+1}_a_que_fue"] = ""
                row[f"c{i+1}_cuando_termina"] = ""

    # --------- eventos concretos ----------
    def _evento_llegada(self):
        self.clock = self.next_arrival
        self.iteration += 1

        cid = self.next_client_id
        self.next_client_id += 1

        c = Cliente(cid, hora_llegada=self.clock)

        r_trx = random.random()
        tipo = self._elige_transaccion(r_trx)
        c.a_que_fue = tipo

        asignado = None
        if not self._hay_alguien_en_cola():
            libre = self._primer_bibliotecario_libre()
            if libre is not None:
                asignado = libre

        if asignado is None:
            c.estado = "EN COLA"
            c.hora_entrada_cola = self.clock
            self.cola.append(c.id)
        else:
            c.estado = f"SIENDO ATENDIDO({asignado+1})"
            r_srv, demora = self._demora_por_transaccion(tipo)
            b = self.bib[asignado]
            b.estado = "OCUPADO"
            b.rnd = fmt(r_srv)
            b.demora = fmt(demora)
            b.hora_num = self.clock + demora
            b.hora = fmt(b.hora_num)
            b.cliente_id = c.id

        self.clientes[c.id] = c

        # programar próxima llegada
        self.next_arrival = self.clock + self.t_inter

        row = {
            "iteracion": self.iteration,
            "evento": "LLEGADA_CLIENTE",
            "reloj": fmt(self.clock, 2),
            "lleg_tiempo": fmt(self.t_inter, 2),
            "lleg_minuto": fmt(self.next_arrival, 2),
            "trx_rnd": fmt(r_trx, 4),
            "trx_tipo": tipo,
            "lee_rnd": "", "lee_lugar": "", "lee_tiempo": "", "lee_fin": "",
            "b1_estado": self.bib[0].estado, "b1_rnd": self.bib[0].rnd,
            "b1_demora": self.bib[0].demora, "b1_hora": self.bib[0].hora,
            "b2_estado": self.bib[1].estado, "b2_rnd": self.bib[1].rnd,
            "b2_demora": self.bib[1].demora, "b2_hora": self.bib[1].hora,
            "cola": len(self.cola),
            "biblio_estado": self.biblio_estado, "biblio_personas": self.biblio_personas,
            "est_b1_libre": fmt(self.est_b1_libre), "est_b2_libre": fmt(self.est_b2_libre),
            "est_bib_ocioso_acum": fmt(self.est_bib_ocioso_acum),
            "est_cli_perm_acum": fmt(self.est_cli_perm_acum),
        }
        self._llenar_bloque_clientes(row)
        return row

    def _evento_fin_atencion(self, i):
        idx = i - 1
        b = self.bib[idx]
        # mover reloj a ese fin
        self.clock = b.hora_num
        self.iteration += 1

        cid = b.cliente_id
        cli = self.clientes[cid]

        # decide destino del cliente
        lee_rnd = ""
        lee_lugar = ""
        lee_tiempo = ""
        lee_fin = ""

        if cli.a_que_fue == "Pedir":
            r = random.random()
            lee_rnd = fmt(r, 4)
            if r < self.p_retira:
                # se retira a casa
                lee_lugar = "Casa"
                cli.estado = "L"
                cli.fin_lect_num = None
                cli.cuando_termina_leer = ""
            else:
                # se queda en biblioteca leyendo
                lee_lugar = "Biblioteca"
                cli.estado = "EC LEYENDO"
                fin_lec = self.clock + self.t_lect_biblio
                cli.fin_lect_num = fin_lec
                cli.cuando_termina_leer = fmt(fin_lec, 2)
                lee_tiempo = fmt(self.t_lect_biblio, 2)  # solo esta fila
                lee_fin = cli.cuando_termina_leer
        else:
            # consulta/devolver -> destrucción
            cli.estado = "L"
            cli.fin_lect_num = None
            cli.cuando_termina_leer = ""

        # liberar bibliotecario y tomar de cola si hay
        b.estado = "LIBRE"; b.rnd = ""; b.demora = ""; b.hora = ""; b.hora_num = None; b.cliente_id = None
        self._tomar_de_cola(idx)

        row = self._armar_fila_base(f"FIN_ATENCION_{i}({cid})")
        row["lee_rnd"] = lee_rnd
        row["lee_lugar"] = lee_lugar
        row["lee_tiempo"] = lee_tiempo
        row["lee_fin"] = lee_fin
        return row

    def _evento_fin_lectura(self, cid):
        # reloj salta a fin de lectura de ese cliente
        c = self.clientes[cid]
        self.clock = c.fin_lect_num
        self.iteration += 1

        # va a devolver; intenta atención directa o cola
        c.fin_lect_num = None
        c.cuando_termina_leer = ""
        c.a_que_fue = "Devolver"  # única acción después de leer

        libre = self._primer_bibliotecario_libre()
        if libre is not None:
            c.estado = f"SIENDO ATENDIDO({libre+1})"
            r_srv, demora = self._demora_por_transaccion("Devolver")
            b = self.bib[libre]
            b.estado = "OCUPADO"
            b.rnd = fmt(r_srv)
            b.demora = fmt(demora)
            b.hora_num = self.clock + demora
            b.hora = fmt(b.hora_num)
            b.cliente_id = c.id
        else:
            c.estado = "EN COLA"
            c.hora_entrada_cola = self.clock
            self.cola.append(c.id)

        row = self._armar_fila_base(f"FIN_LECTURA({cid})")
        return row

    # --------- paso general ----------
    def siguiente_evento(self):
        """Decide y ejecuta el próximo evento. Devuelve la fila para el vector."""
        ne = self._proximo_evento()
        if ne is None:
            raise StopIteration("No hay eventos pendientes.")
        t, _, tipo, data = ne

        # cortar por límites
        if self.iteration >= self.iter_limit:
            raise StopIteration("Se alcanzó el máximo de iteraciones.")
        if t > self.time_limit:
            raise StopIteration("Se alcanzó el tiempo límite de simulación.")

        if tipo == "llegada":
            return self._evento_llegada()
        elif tipo == "fin_atencion":
            return self._evento_fin_atencion(data["i"])
        elif tipo == "fin_lectura":
            return self._evento_fin_lectura(data["cid"])
        else:
            raise RuntimeError("Tipo de evento desconocido.")

# ----------------- 2ª Ventana (Vector de Estado) -----------------
class SimulationWindow(tk.Toplevel):
    def __init__(self, master, config_dict, num_clientes=NUM_CLIENTES):
        super().__init__(master)
        self.title("Vector de Estado - Simulación")
        self.geometry("1280x720")
        self.minsize(1040, 520)

        self.engine = SimulationEngine(config_dict, max_slots_clientes=num_clientes)

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # Barra superior
        top = ttk.Frame(root)
        top.pack(fill="x")
        resumen = ttk.Label(
            top,
            text=(f"Config → X={config_dict['simulacion']['tiempo_limite_min']} min | "
                  f"N={config_dict['simulacion']['iteraciones_max']} | "
                  f"i={config_dict['simulacion']['mostrar_vector_estado']['i_iteraciones']} "
                  f"desde j={config_dict['simulacion']['mostrar_vector_estado']['desde_minuto_j']}  "
                  f"| t_entre_llegadas={config_dict['llegadas']['tiempo_entre_llegadas_min']} min"),
            foreground="#374151"
        )
        resumen.pack(side="left")
        ttk.Button(top, text="Siguiente evento", command=self.on_next).pack(side="right")

        # ------ columnas ------
        self.columns = []
        def add_col(cid, text, w): self.columns.append({"id": cid, "text": text, "w": w})

        add_col("iteracion", "Numero de iteracion", 160)
        add_col("evento", "Evento", 160)
        add_col("reloj", "Reloj (minutos)", 130)
        # LLEGADA_CLIENTE
        add_col("lleg_tiempo", "TIEMPO", 90)
        add_col("lleg_minuto", "MINUTO QUE LLEGA", 165)
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
        # CLIENTES al final
        self.cliente_groups = []
        for i in range(1, num_clientes + 1):
            start_idx = len(self.columns)
            add_col(f"c{i}_estado", "ESTADO", 100)
            add_col(f"c{i}_hora_llegada", "HORA_LLEGADA", 130)
            add_col(f"c{i}_a_que_fue", "A QUE FUE", 120)
            add_col(f"c{i}_cuando_termina", "Cuando termina de leer", 180)
            end_idx = len(self.columns) - 1
            self.cliente_groups.append((f"CLIENTE {i}", start_idx, end_idx))

        # grupos superiores
        self.groups = [
            ("", 0, 2),
            ("LLEGADA_CLIENTE", 3, 4),
            ("TRANSACCION", 5, 6),
            ("¿Dónde Lee? - solo si pide Libro (cuenta luego de la atención)", 7, 10),
            ("BIBLIOTECARIO 1", 11, 14),
            ("BIBLIOTECARIO 2", 15, 18),
            ("COLA", 19, 19),
            ("BIBLIOTECA", 20, 21),
            ("ESTADISTICAS · BIBLIOTECARIOS", 22, 24),
            ("ESTADISTICAS · CLIENTES", 25, 25),
            *self.cliente_groups,
        ]

        # header + tree
        wrapper = ttk.Frame(root)
        wrapper.pack(fill="both", expand=True)

        self.header_canvas = tk.Canvas(wrapper, height=28, background="#ffffff", highlightthickness=0)
        self.header_canvas.pack(fill="x", side="top")

        self.tree = ttk.Treeview(wrapper, show="headings", height=18)
        self.tree.pack(fill="both", expand=True, side="left")

        yscroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        yscroll.pack(fill="y", side="right")
        xscroll = ttk.Scrollbar(root, orient="horizontal")
        xscroll.pack(fill="x", side="bottom")

        def on_xscroll(*args):
            self.tree.xview(*args); self.header_canvas.xview(*args)
        def on_tree_xscroll(lo, hi):
            xscroll.set(lo, hi); self.header_canvas.xview_moveto(lo)

        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=on_tree_xscroll)
        xscroll.configure(command=on_xscroll)

        self.col_ids = [c["id"] for c in self.columns]
        self.tree["columns"] = self.col_ids
        for c in self.columns:
            self.tree.heading(c["id"], text=c["text"], anchor="center")
            self.tree.column(c["id"], width=c["w"], minwidth=40, anchor="center", stretch=False)

        self._draw_group_headers()
        self.bind("<Configure>", lambda e: self._draw_group_headers())
        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), 28))

        # Fila de INICIALIZACION
        self._insert_initialization_row()

    # --- dibujo de grupos ---
    def _total_width(self):
        return sum(self.tree.column(c["id"], option="width") for c in self.columns)

    def _col_x_positions(self):
        xs, acc = [], 0
        for c in self.columns:
            w = self.tree.column(c["id"], option="width")
            xs.append((acc, acc + w)); acc += w
        return xs

    def _draw_group_headers(self):
        self.header_canvas.delete("all")
        xs = self._col_x_positions(); h = 28
        for text, i0, i1 in self.groups:
            x0 = xs[i0][0]; x1 = xs[i1][1]
            self.header_canvas.create_rectangle(x0, 0, x1, h, fill=GROUP_BG, outline=GROUP_BORDER)
            self.header_canvas.create_text((x0 + x1) / 2, h / 2, text=text, anchor="center",
                                           font=("Segoe UI", 9, "bold"))
        for x0, x1 in xs:
            self.header_canvas.create_line(x1, 0, x1, h, fill="#e5e7eb")
        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), h))

    # --- inicialización ---
    def _insert_initialization_row(self):
        eng = self.engine
        init = {
            "iteracion": 0, "evento": "INICIALIZACION", "reloj": fmt(eng.clock, 2),
            "lleg_tiempo": fmt(eng.t_inter, 2), "lleg_minuto": fmt(eng.next_arrival, 2),
            "trx_rnd": "", "trx_tipo": "", "lee_rnd": "", "lee_lugar": "",
            "lee_tiempo": "", "lee_fin": "",
            "b1_estado": "LIBRE", "b1_rnd": "", "b1_demora": "", "b1_hora": "",
            "b2_estado": "LIBRE", "b2_rnd": "", "b2_demora": "", "b2_hora": "",
            "cola": 0, "biblio_estado": "", "biblio_personas": 0,
            "est_b1_libre": fmt(0), "est_b2_libre": fmt(0),
            "est_bib_ocioso_acum": fmt(0), "est_cli_perm_acum": fmt(0),
        }
        for i in range(NUM_CLIENTES):
            init[f"c{i+1}_estado"] = ""
            init[f"c{i+1}_hora_llegada"] = ""
            init[f"c{i+1}_a_que_fue"] = ""
            init[f"c{i+1}_cuando_termina"] = ""
        values = [init.get(cid, "") for cid in self.col_ids]
        self.tree.insert("", "end", values=values)

    # --- acción: siguiente evento ---
    def on_next(self):
        try:
            if not self.engine.hay_mas():
                messagebox.showinfo("Simulación", "No hay más eventos (límite de tiempo o iteraciones alcanzado).")
                return
            row_dict = self.engine.siguiente_evento()
        except StopIteration as e:
            messagebox.showinfo("Simulación", str(e))
            return
        values = [row_dict.get(cid, "") for cid in self.col_ids]
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

        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)

        self.fields = {}

        # 1) SIMULACIÓN
        sim = ttk.LabelFrame(root, text="1) Simulación (todo en minutos)")
        sim.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sim.columnconfigure(1, weight=1)

        self._mk_int(sim, "tiempo_limite", "Tiempo límite X", 60, 1, 10_000,
                     "La simulación termina al llegar a X o a N iteraciones (lo que ocurra primero).")
        self._mk_int(sim, "iteraciones_max", "Cantidad de iteraciones N", 1000, 1, 100_000,
                     "Máximo permitido: 100000.")
        self._mk_int(sim, "i_mostrar", "i (iteraciones a mostrar)", 10, 1, 100_000,
                     "Cuántas iteraciones del vector de estado se listarán.")
        self._mk_int(sim, "j_inicio", "j (minuto de inicio)", 0, 0, 10_000,
                     "Minuto desde el cual se comienzan a mostrar las i iteraciones.")

        # 2) LLEGADAS
        lleg = ttk.LabelFrame(root, text="2) Llegadas")
        lleg.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        lleg.columnconfigure(1, weight=1)
        self._mk_int(lleg, "t_entre_llegadas", "Tiempo entre llegadas (min)", 4, 1, 10_000,
                     "Entero en minutos (por defecto 4).")

        # 3) MOTIVOS %
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

        # 4) CONSULTAS (Uniforme)
        cons = ttk.LabelFrame(root, text="4) Consultas — Distribución Uniforme(A, B) en minutos")
        cons.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        cons.columnconfigure(1, weight=1)
        self._mk_int(cons, "uni_a", "A (min)", 2, 0, 10_000, "Debe cumplirse A < B y A ≠ B.")
        self._mk_int(cons, "uni_b", "B (min)", 5, 0, 10_000)

        # 5) LECTURA
        lect = ttk.LabelFrame(root, text="5) Lectura")
        lect.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        lect.columnconfigure(1, weight=1)
        self._mk_int(lect, "pct_retira", "Se retira a leer en casa (%)", 60, 0, 100, on_change=self._update_queda)
        fila_queda = ttk.Frame(lect)
        fila_queda.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(fila_queda, text="Se queda a leer en biblioteca (%)").pack(side="left")
        self.lbl_queda = ttk.Label(fila_queda, text="40")
        self.lbl_queda.pack(side="left", padx=8)
        self._mk_int(lect, "t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 30, 1, 10_000,
                     "Entero positivo (no 0).")

        # 6) SALIDA (JSON en la misma pantalla)
        salida = ttk.LabelFrame(root, text="6) Resultado")
        salida.grid(row=5, column=0, sticky="nsew", pady=(0, 8))
        root.rowconfigure(5, weight=1)
        salida.columnconfigure(0, weight=1)
        self.txt_out = tk.Text(salida, height=10)
        self.txt_out.grid(row=0, column=0, sticky="nsew")
        salida.rowconfigure(0, weight=1)

        # Botonera
        btns = ttk.Frame(root)
        btns.grid(row=6, column=0, sticky="e")
        ttk.Button(btns, text="Restablecer", command=self.reset_defaults).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="Generar", command=self.on_generate).grid(row=0, column=1)

        self.reset_defaults()
        self._update_pct_sum()
        self._update_queda()

    # ---------- Component factory ----------
    def _mk_int(self, parent, key, label, default, lo, hi, help_=None, on_change=None):
        row = ttk.Frame(parent)
        r = max((child.grid_info().get("row", -1) for child in parent.winfo_children()
                 if isinstance(child, ttk.Frame)), default=-1) + 1
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

        self.fields[key] = {"var": var, "entry": ent, "lo": lo, "hi": hi, "default": default}
        if on_change:
            var.trace_add("write", lambda *args: on_change())
        return ent

    # ---------- Live feedback ----------
    def _update_pct_sum(self):
        s = 0
        for k in ("pct_pedir", "pct_devolver", "pct_consultar"):
            v = int_or_none(self.fields[k]["var"].get())
            s += v if v is not None else 0
        self.lbl_sum.configure(text=f"{s}%")
        self.lbl_sum.configure(style="Ok.TLabel" if s == 100 else "Bad.TLabel")

    def _update_queda(self):
        p = int_or_none(self.fields["pct_retira"]["var"].get())
        if p is None:
            p = 0
        self.lbl_queda.configure(text=str(max(0, min(100, 100 - p))))

    # ---------- Defaults ----------
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
            "t_lectura_biblio": 30
        }
        for k, meta in self.fields.items():
            meta["entry"].configure(style="TEntry")
        for k, v in defaults.items():
            self.fields[k]["var"].set(str(v))
        self.txt_out.delete("1.0", "end")

    # ---------- Generar (validar + abrir 2ª pantalla) ----------
    def on_generate(self):
        for meta in self.fields.values():
            meta["entry"].configure(style="TEntry")

        errors, mark = [], []
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
        if None not in (p_ped, p_dev, p_con) and (p_ped + p_dev + p_con != 100):
            errors.append(f"• La suma de motivos debe ser 100% (ahora {p_ped+p_dev+p_con}%).")
            mark += ["pct_pedir", "pct_devolver", "pct_consultar"]

        a = need_int("uni_a", "Uniforme A (min)", 0, 10_000)
        b = need_int("uni_b", "Uniforme B (min)", 0, 10_000)
        if None not in (a, b):
            if a == b: errors.append("• En Uniforme(A,B) debe cumplirse A ≠ B."); mark += ["uni_a", "uni_b"]
            if a > b: errors.append("• En Uniforme(A,B) debe cumplirse A < B."); mark += ["uni_a", "uni_b"]

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
                "mostrar_vector_estado": {"i_iteraciones": i_mos, "desde_minuto_j": j_ini}
            },
            "llegadas": {"tiempo_entre_llegadas_min": t_lleg},
            "motivos": {
                "pedir_libros_pct": p_ped,
                "devolver_libros_pct": p_dev,
                "consultar_socios_pct": p_con
            },
            "consultas_uniforme": {"a_min": a, "b_min": b},
            "lectura": {
                "retira_casa_pct": p_ret,
                "queda_biblioteca_pct": 100 - p_ret,
                "tiempo_fijo_biblioteca_min": t_bib
            }
        }

        self.txt_out.delete("1.0", "end")
        pretty = json.dumps(cfg, indent=2, ensure_ascii=False)
        self.txt_out.insert("1.0", pretty)
        self.clipboard_clear(); self.clipboard_append(pretty)

        SimulationWindow(self, cfg, num_clientes=NUM_CLIENTES)

if __name__ == "__main__":
    App().mainloop()

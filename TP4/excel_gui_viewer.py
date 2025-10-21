import tkinter as tk
from tkinter import ttk, messagebox
import json

APP_TITLE = "Parámetros de Simulación - Biblioteca (Una sola pantalla)"
GROUP_BG = "#e8efff"   # color para encabezados de grupo
GROUP_BORDER = "#a8b3d7"
NUM_CLIENTES = 3       # <<<<< cambialo si querés más/menos clientes (CLIENTE 1..N)

# ---------- Utils ----------
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


# ========== Ventana de Vector de Estado (2da pantalla) ==========
class SimulationWindow(tk.Toplevel):
    """
    Muestra el Vector de Estado con cabeceras agrupadas,
    preparada para recibir filas desde la simulación.
    """
    def __init__(self, master, config_dict, num_clientes=NUM_CLIENTES):
        super().__init__(master)
        self.title("Vector de Estado - Simulación")
        self.geometry("1280x640")
        self.minsize(1040, 480)

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        resumen = ttk.Label(
            root,
            text=(
                f"Config: X={config_dict['simulacion']['tiempo_limite_min']} min | "
                f"N={config_dict['simulacion']['iteraciones_max']} | "
                f"i={config_dict['simulacion']['mostrar_vector_estado']['i_iteraciones']} "
                f"desde j={config_dict['simulacion']['mostrar_vector_estado']['desde_minuto_j']}"
            ),
            foreground="#374151"
        )
        resumen.pack(anchor="w", pady=(0, 6))

        # ---- Definir columnas base ----
        self.columns = []

        def add_col(cid, text, w):
            self.columns.append({"id": cid, "text": text, "w": w})

        # 0) Nueva columna inicial:
        add_col("iteracion", "Numero de iteracion", 160)

        # Evento / Reloj
        add_col("evento", "Evento", 110)
        add_col("reloj", "Reloj (minutos)", 130)

        # LLEGADA_CLIENTE
        add_col("lleg_tiempo", "TIEMPO", 90)
        add_col("lleg_minuto", "MINUTO QUE LLEGA", 165)

        # TRANSACCION
        add_col("trx_rnd", "RND", 70)
        add_col("trx_tipo", "Tipo Transaccion", 160)

        # ¿Dónde Lee?
        add_col("lee_rnd", "RND", 70)
        add_col("lee_lugar", "LUGAR", 110)
        add_col("lee_tiempo", "TIEMPO", 100)
        add_col("lee_fin", "Fin Lectura", 130)

        # BIBLIOTECARIO 1
        add_col("b1_estado", "Estado", 90)
        add_col("b1_rnd", "RND", 70)
        add_col("b1_demora", "Demora", 100)
        add_col("b1_hora", "Hora", 110)

        # BIBLIOTECARIO 2
        add_col("b2_estado", "Estado", 90)
        add_col("b2_rnd", "RND", 70)
        add_col("b2_demora", "Demora", 100)
        add_col("b2_hora", "Hora", 110)

        # COLA
        add_col("cola", "COLA", 90)

        # BIBLIOTECA
        add_col("biblio_estado", "Estado", 95)
        add_col("biblio_personas", "Personas en la biblioteca (MAXIMO 20)", 270)

        # ESTADISTICAS - BIBLIOTECARIOS
        add_col("est_b1_libre", "TIEMPO LIBRE BIBLIOTECARIO1", 230)
        add_col("est_b2_libre", "TIEMPO LIBRE BIBLIOTECARIO2", 230)
        add_col("est_bib_ocioso_acum", "ACUMULADOR TIEMPO OCIOSO BIBLIOTECARIOS", 330)

        # ESTADISTICAS - CLIENTES
        add_col("est_cli_perm_acum", "ACUMULADOR TIEMPO PERMANENCIA", 270)

        # CLIENTES (objetos temporales) al final: CLIENTE 1..N con 4 subcolumnas
        self.cliente_groups = []  # para pintar grupos "CLIENTE 1", "CLIENTE 2", ...
        for i in range(1, max(1, int(num_clientes)) + 1):
            start_idx = len(self.columns)
            add_col(f"c{i}_estado", "ESTADO", 100)
            add_col(f"c{i}_hora_llegada", "HORA_LLEGADA", 130)
            add_col(f"c{i}_a_que_fue", "A QUE FUE", 120)
            add_col(f"c{i}_cuando_termina", "Cuando termina de leer", 180)
            end_idx = len(self.columns) - 1
            self.cliente_groups.append((f"CLIENTE {i}", start_idx, end_idx))

        # ---- Grupos (fila superior de cabeceras) ----
        # (Etiqueta, índice columna inicial, índice columna final)
        self.groups = [
            ("", 0, 2),  # Numero de iteracion + Evento + Reloj
            ("LLEGADA_CLIENTE", 3, 4),
            ("TRANSACCION", 5, 6),
            ("¿Dónde Lee? - solo si pide Libro (cuenta luego de la atención)", 7, 10),
            ("BIBLIOTECARIO 1", 11, 14),
            ("BIBLIOTECARIO 2", 15, 18),
            ("COLA", 19, 19),
            ("BIBLIOTECA", 20, 21),
            ("ESTADISTICAS · BIBLIOTECARIOS", 22, 24),
            ("ESTADISTICAS · CLIENTES", 25, 25),
            # Luego agregamos dinámicamente los grupos de clientes:
            *self.cliente_groups,
        ]

        # ---- Header Canvas (grupos) + Treeview (hojas) ----
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
            self.tree.xview(*args)
            self.header_canvas.xview(*args)

        def on_tree_xscroll(lo, hi):
            xscroll.set(lo, hi)
            self.header_canvas.xview_moveto(lo)

        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=on_tree_xscroll)
        xscroll.configure(command=on_xscroll)

        # Configurar columnas hoja
        self.col_ids = [c["id"] for c in self.columns]
        self.tree["columns"] = self.col_ids
        for c in self.columns:
            self.tree.heading(c["id"], text=c["text"], anchor="center")
            self.tree.column(c["id"], width=c["w"], minwidth=40, anchor="center", stretch=False)

        # Dibujar la fila superior de grupos (con color distintivo)
        self._draw_group_headers()

        # Redibujar en resize
        self.bind("<Configure>", lambda e: self._draw_group_headers())
        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), 28))

        # Fila placeholder para ver el ancho
        self.tree.insert("", "end", values=[""] * len(self.col_ids))

        # API pública para agregar filas desde la simulación:
        # self.add_rows([...])

    def _total_width(self):
        return sum(self.tree.column(c["id"], option="width") for c in self.columns)

    def _col_x_positions(self):
        xs, acc = [], 0
        for c in self.columns:
            w = self.tree.column(c["id"], option="width")
            xs.append((acc, acc + w))
            acc += w
        return xs

    def _draw_group_headers(self):
        self.header_canvas.delete("all")
        xs = self._col_x_positions()
        h = 28
        for text, i0, i1 in self.groups:
            x0 = xs[i0][0]
            x1 = xs[i1][1]
            # bloque de grupo con color diferenciado
            self.header_canvas.create_rectangle(x0, 0, x1, h, fill=GROUP_BG, outline=GROUP_BORDER)
            self.header_canvas.create_text((x0 + x1) / 2, h / 2, text=text, anchor="center", font=("Segoe UI", 9, "bold"))
        # líneas guía fin de columna
        for x0, x1 in xs:
            self.header_canvas.create_line(x1, 0, x1, h, fill="#e5e7eb")
        self.header_canvas.configure(scrollregion=(0, 0, self._total_width(), h))

    def add_rows(self, rows):
        """rows: lista de iterables del mismo largo que self.col_ids"""
        for r in rows:
            if len(r) != len(self.col_ids):
                raise ValueError(f"Fila con {len(r)} valores; se esperaban {len(self.col_ids)}.")
            self.tree.insert("", "end", values=r)


# ========== Ventana principal (configuración) ==========
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(900, 680)

        # Estilos
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

        def only_digits(P):  # permitir vacío mientras se escribe
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

    # ---------- Generar (validar + abrir 2da pantalla) ----------
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

        # Simulación
        t_lim = need_int("tiempo_limite", "Tiempo límite X", 1, 10_000)
        n_max = need_int("iteraciones_max", "Cantidad de iteraciones N", 1, 100_000)
        i_mos = need_int("i_mostrar", "i (iteraciones a mostrar)", 1, 100_000)
        j_ini = need_int("j_inicio", "j (minuto de inicio)", 0, 10_000)

        if None not in (t_lim, j_ini) and j_ini >= t_lim:
            errors.append("• j (minuto de inicio) debe ser menor que X (tiempo límite).")
            mark += ["j_inicio", "tiempo_limite"]

        if None not in (i_mos, n_max) and i_mos > n_max:
            errors.append("• i no debería exceder N (cantidad de iteraciones).")
            mark += ["i_mostrar", "iteraciones_max"]

        # Llegadas
        t_lleg = need_int("t_entre_llegadas", "Tiempo entre llegadas (min)", 1, 10_000)

        # Motivos %
        p_ped = need_int("pct_pedir", "Pedir libros (%)", 0, 100)
        p_dev = need_int("pct_devolver", "Devolver libros (%)", 0, 100)
        p_con = need_int("pct_consultar", "Consultar hacerse socio (%)", 0, 100)
        if None not in (p_ped, p_dev, p_con):
            s = p_ped + p_dev + p_con
            if s != 100:
                errors.append(f"• La suma de motivos debe ser 100% (suma actual: {s}%).")
                mark += ["pct_pedir", "pct_devolver", "pct_consultar"]

        # Uniforme
        a = need_int("uni_a", "Uniforme A (min)", 0, 10_000)
        b = need_int("uni_b", "Uniforme B (min)", 0, 10_000)
        if None not in (a, b):
            if a == b:
                errors.append("• En Uniforme(A, B) debe cumplirse A ≠ B.")
                mark += ["uni_a", "uni_b"]
            if a > b:
                errors.append("• En Uniforme(A, B) debe cumplirse A < B.")
                mark += ["uni_a", "uni_b"]

        # Lectura
        p_ret = need_int("pct_retira", "Se retira a leer en casa (%)", 0, 100)
        t_bib = need_int("t_lectura_biblio", "Tiempo fijo en biblioteca (min)", 1, 10_000)

        if errors:
            for k in set(mark):
                self.fields[k]["entry"].configure(style="Invalid.TEntry")
            messagebox.showerror("Validación", "Revisá los siguientes puntos:\n\n" + "\n".join(errors))
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

        # Mostrar JSON (como antes)
        self.txt_out.delete("1.0", "end")
        pretty = json.dumps(cfg, indent=2, ensure_ascii=False)
        self.txt_out.insert("1.0", pretty)
        self.clipboard_clear()
        self.clipboard_append(pretty)

        # Abrir 2da pantalla con columnas actualizadas
        SimulationWindow(self, cfg, num_clientes=NUM_CLIENTES)


if __name__ == "__main__":
    App().mainloop()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulación de colas (Biblioteca) con interfaz Tkinter — v3
Cambios:
- RNG sin semilla fija (aleatorio real).
- Llegadas ~ Exp(mean=4): Δt = -4 * ln(1-U).
- Cada llegada crea un cliente y el evento ARRIVAL incluye ese cliente para mostrarse en tabla.
- Transacción muestra RND y tipo: Pide Libro / Devuelve Libro / Consulta.
- Se muestran RNDs de llegada, servicio, decisión de lectura y tiempo de lectura.
- Estado de cola: "Ocupado" si hay gente esperando, "Libre" si no.
- Cola (contador) ++ con llegada sin servicio inmediato; -- cuando se inicia un servicio.
- Scroll horizontal y "doble encabezado" aproximado con etiquetas agrupadas sobre la tabla.
"""

import math
import random
import heapq
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Deque
from collections import deque

import tkinter as tk
from tkinter import ttk, messagebox

# ========================== Utilidades de tiempo ==========================

def hhmm_to_minutes(s: str) -> int:
    s = s.strip()
    if ":" not in s:
        return int(float(s))
    hh, mm = s.split(":")
    return int(hh) * 60 + int(mm)

def minutes_to_hhmm(m: float) -> str:
    m = max(0, m)
    hh = int(m // 60)
    mm = int(round(m - hh*60))
    if mm == 60:
        hh += 1
        mm = 0
    return f"{hh:02d}:{mm:02d}"

# ====================== Generadores de aleatorios ========================

def rnd() -> float:
    return random.random()

def exp_mean(mean: float, u: Optional[float] = None) -> Tuple[float, float]:
    if u is None:
        u = rnd()
    x = -mean * math.log(1.0 - u + 1e-15)
    return x, u

def unif(a: float, b: float, u: Optional[float] = None) -> Tuple[float, float]:
    if u is None:
        u = rnd()
    return (a + (b - a) * u, u)

# =========================== Entidades ===================================

_client_seq = 0

@dataclass
class Client:
    id: int
    arrival_time: float
    request_type: str  # 'borrow' | 'return' | 'consult'
    will_read_here: Optional[bool] = None
    read_end_time: Optional[float] = None
    service_start: Optional[float] = None  # para calcular espera
    state_code: str = "EC"  # "SA", "EC", "EL"

def next_client_id() -> int:
    global _client_seq
    _client_seq += 1
    return _client_seq

@dataclass
class Employee:
    id: int
    busy: bool = False
    current_client: Optional[Client] = None
    busy_until: float = 0.0
    total_idle_time: float = 0.0
    last_idle_from: float = 0.0

    @property
    def code(self) -> str:
        return "Ocupado" if self.busy else "Libre"

# ========================== Eventos ======================================

EV_ARRIVAL = "Llega_Cliente"
EV_END_SERVICE = "Sale_Cliente"
EV_END_READING = "En_Lectura"

@dataclass(order=True)
class Event:
    time: float
    priority: int
    ev_type: str = field(compare=False)
    employee_id: Optional[int] = field(default=None, compare=False)
    client: Optional[Client] = field(default=None, compare=False)
    rnd_info: List[Tuple[str, float]] = field(default_factory=list, compare=False)
    extra_info: dict = field(default_factory=dict, compare=False)

# ====================== Simulador principal ==============================

class LibrarySimulation:
    def __init__(self, sim_time_limit: float):
        self.time_limit = sim_time_limit

        # Estado
        self.clock = 0.0
        self.queue: Deque[Client] = deque()
        self.employees = [Employee(id=1, busy=False, last_idle_from=0.0),
                          Employee(id=2, busy=False, last_idle_from=0.0)]
        self.reading_inside: List[Client] = []
        self.is_open = True

        # Estadísticas
        self.num_attended = 0
        self.sum_time_in_system = 0.0
        self.max_queue_len = 0
        self.events_processed = 0
        self.queue_counter = 0  # contador visual de cola FIFO

        # Calendario
        self.calendar: List[Event] = []
        self._schedule_first_arrival()

    # -------------------- programación de eventos --------------------

    def _schedule_first_arrival(self):
        dt, u = exp_mean(4.0)
        ev = Event(time=dt, priority=0, ev_type=EV_ARRIVAL)
        ev.rnd_info.append(("RND_LLEGADA", u))
        ev.extra_info["DELTA_ARRIBO"] = dt
        heapq.heappush(self.calendar, ev)

    def _schedule_next_arrival(self, now: float):
        dt, u = exp_mean(4.0)
        ev = Event(time=now + dt, priority=0, ev_type=EV_ARRIVAL)
        ev.rnd_info.append(("RND_LLEGADA", u))
        ev.extra_info["DELTA_ARRIBO"] = dt
        heapq.heappush(self.calendar, ev)

    def _schedule_end_service(self, now: float, emp: Employee, client: Client, service_time: float, rnd_pairs: List[Tuple[str, float]]):
        emp.busy = True
        emp.current_client = client
        emp.busy_until = now + service_time
        ev = Event(time=emp.busy_until, priority=1, ev_type=EV_END_SERVICE, employee_id=emp.id, client=client, rnd_info=rnd_pairs)
        heapq.heappush(self.calendar, ev)

    def _schedule_end_reading(self, client: Client, end_time: float, rnd_pairs: List[Tuple[str, float]]):
        ev = Event(time=end_time, priority=2, ev_type=EV_END_READING, client=client, rnd_info=rnd_pairs)
        heapq.heappush(self.calendar, ev)

    # --------------------------- Lógica --------------------------------

    def _maybe_open_or_close(self):
        self.is_open = len(self.reading_inside) < 20

    def _choose_request_type(self) -> Tuple[str, float]:
        u = rnd()
        if u < 0.45:
            return "borrow", u
        elif u < 0.90:
            return "return", u
        else:
            return "consult", u

    def _service_time(self, req_type: str) -> Tuple[float, List[Tuple[str,float]]]:
        if req_type == "borrow":
            t, u = exp_mean(6.0)
            return t, [("RND_SERV_BORROW", u)]
        elif req_type == "return":
            t, u = unif(1.5, 2.5)
            return t, [("RND_SERV_RETURN", u)]
        else:
            t, u = unif(2.0, 5.0)
            return t, [("RND_SERV_CONSULT", u)]

    def _will_read_here(self) -> Tuple[bool, float]:
        u = rnd()
        return (u < 0.40, u)

    def _reading_time(self) -> Tuple[float, float]:
        t, u = exp_mean(30.0)
        return t, u

    def _assign_to_employee_if_possible(self, now: float):
        for emp in self.employees:
            if (not emp.busy) and self.queue:
                client = self.queue.popleft()
                self.queue_counter = max(0, self.queue_counter - 1)
                emp.total_idle_time += max(0.0, now - emp.last_idle_from)
                client.service_start = now
                client.state_code = "SA"
                st, rnd_list = self._service_time(client.request_type)
                self._schedule_end_service(now, emp, client, st, rnd_list)

    # --------------------------- Vector de estado -----------------------

    COLUMNS = [
        "Evento","Reloj","RND Lleg","Δt Lleg","Hora",
        "Cola Estado","Cola (n)",
        "RND Trans","Tipo Transaccion","Estado Trans",
        "1 RND","1 Demora","1 Estado",
        "2 RND","2 Demora","2 Estado",
        "ACUM TPO EN SIST","CLIENTES ATENDIDOS","Prom PERMANENCIA","ACUM Tiempo libre Empleado",
        "ID","Hora llegada","Tiempo Espera","Estado Cliente",
        "¿Lee? RND","Lugar","Lect RND","Lect Tiempo","Lect Hora"
    ]

    def _row_from_event(self, ev: Event):
        emp1 = self.employees[0]
        emp2 = self.employees[1]

        rnd_lleg = None
        delta_lleg = ""
        for tag,val in ev.rnd_info:
            if tag == "RND_LLEGADA":
                rnd_lleg = val
                delta_lleg = f"{ev.extra_info.get('DELTA_ARRIBO', ''):.2f}" if 'DELTA_ARRIBO' in ev.extra_info else ""

        rnd_trans = ""
        tipo_trans = ""
        estado_trans = ""

        emp1_rnd = ""
        emp1_demora = ""
        emp1_estado = emp1.code
        emp2_rnd = ""
        emp2_demora = ""
        emp2_estado = emp2.code

        client = ev.client
        cid = client.id if client else ""
        hora_llegada_cliente = minutes_to_hhmm(client.arrival_time) if client else ""
        espera_txt = ""
        estado_cliente = client.state_code if client else ""

        lee_rnd = ""
        lee_lugar = ""
        lect_rnd = ""
        lect_tiempo = ""
        lect_hora = ""

        if ev.ev_type == EV_END_SERVICE and client:
            emp_rnd_val = ""
            for tag,val in ev.rnd_info:
                if tag.startswith("RND_SERV_"):
                    emp_rnd_val = f"{val:.5f}"

            if ev.employee_id == 1:
                emp1_rnd = emp_rnd_val
                emp1_demora = f"{(ev.time - (client.service_start or ev.time)):.2f}"
            else:
                emp2_rnd = emp_rnd_val
                emp2_demora = f"{(ev.time - (client.service_start or ev.time)):.2f}"

            map_tipo = {"borrow": "Pide Libro", "return": "Devuelve Libro", "consult": "Consulta"}
            tipo_trans = map_tipo.get(client.request_type, client.request_type)
            rnd_trans = emp_rnd_val
            estado_trans = "OK"

            if client.service_start is not None:
                espera = max(0.0, client.service_start - client.arrival_time)
                espera_txt = f"{espera:.2f}"
            estado_cliente = "SA"

            if client.request_type == "borrow":
                will_read, u = self._will_read_here()
                client.will_read_here = will_read
                ev.rnd_info.append(("RND_WILL_READ", u))
                if will_read and len(self.reading_inside) < 20:
                    t_read, u_read = self._reading_time()
                    client.read_end_time = self.clock + t_read
                    self.reading_inside.append(client)
                    ev.rnd_info.append(("RND_READ", u_read))
                    self._schedule_end_reading(client, client.read_end_time, rnd_pairs=[("RND_READ", u_read)])
                    client.state_code = "EL"
                else:
                    client.state_code = "SA"

                # set outputs
                lee_rnd = f"{u:.5f}"
                lee_lugar = "SALA" if client.will_read_here and client.read_end_time else "SE RETIRA"
                if client.read_end_time:
                    lect_tiempo = f"{(client.read_end_time - ev.time):.2f}"
                    lect_hora = minutes_to_hhmm(client.read_end_time)
                    for tag,val in ev.rnd_info:
                        if tag == "RND_READ":
                            lect_rnd = f"{val:.5f}"

        prom_perm = (self.sum_time_in_system / self.num_attended) if self.num_attended>0 else 0.0
        acum_idle = self.employees[0].total_idle_time + self.employees[1].total_idle_time
        cola_estado = "Ocupado" if self.queue_counter > 0 else "Libre"
        cola_n = str(self.queue_counter)

        row = [
            ev.ev_type, f"{ev.time:.2f}", (f"{rnd_lleg:.5f}" if rnd_lleg is not None else ""), delta_lleg, minutes_to_hhmm(ev.time),
            cola_estado, cola_n,
            rnd_trans, tipo_trans, estado_trans,
            emp1_rnd, emp1_demora, emp1_estado,
            emp2_rnd, emp2_demora, emp2_estado,
            f"{self.sum_time_in_system:.2f}", str(self.num_attended), f"{prom_perm:.2f}", f"{acum_idle:.2f}",
            str(cid), hora_llegada_cliente, espera_txt, estado_cliente,
            lee_rnd, lee_lugar, lect_rnd, lect_tiempo, lect_hora
        ]
        return row

    # --------------------------- Bucle incremental ----------------------

    def step(self):
        MAX_EVENTS = 100000
        if not self.calendar or self.events_processed >= MAX_EVENTS:
            return None, True

        ev = heapq.heappop(self.calendar)
        if ev.time > self.time_limit:
            return None, True

        self.clock = ev.time

        if ev.ev_type == EV_ARRIVAL:
            self._handle_arrival(ev)
        elif ev.ev_type == EV_END_SERVICE:
            self._handle_end_service(ev)
        elif ev.ev_type == EV_END_READING:
            self._handle_end_reading(ev)

        self.events_processed += 1
        row = self._row_from_event(ev)
        return row, False

    # ------------------------ Handlers de eventos -----------------------

    def _handle_arrival(self, ev: Event):
        if not self.is_open:
            self._schedule_next_arrival(ev.time)
            return

        req_type, u_type = self._choose_request_type()
        c = Client(id=next_client_id(), arrival_time=ev.time, request_type=req_type, state_code="EC")
        ev.client = c
        ev.extra_info["RND_TIPO"] = u_type

        any_free = any(not e.busy for e in self.employees)
        if any_free:
            self.queue.appendleft(c)
            self._assign_to_employee_if_possible(ev.time)
        else:
            self.queue.append(c)
            self.queue_counter += 1
            if len(self.queue) > self.max_queue_len:
                self.max_queue_len = len(self.queue)

        self._schedule_next_arrival(ev.time)

    def _handle_end_service(self, ev: Event):
        emp = next(e for e in self.employees if e.id == ev.employee_id)
        client = emp.current_client

        emp.busy = False
        emp.current_client = None
        emp.busy_until = self.clock
        emp.last_idle_from = self.clock

        time_in_system = self.clock - client.arrival_time
        self.num_attended += 1
        self.sum_time_in_system += time_in_system

        if client.request_type == "borrow":
            # handled in _row_from_event to ensure RND logging aligns
            pass

        self._assign_to_employee_if_possible(ev.time)
        self._maybe_open_or_close()

    def _handle_end_reading(self, ev: Event):
        client = ev.client
        try:
            self.reading_inside.remove(client)
        except ValueError:
            pass

        c2 = Client(id=client.id, arrival_time=self.clock, request_type="return", state_code="EC")
        if any(not e.busy for e in self.employees):
            self.queue.appendleft(c2)
            self._assign_to_employee_if_possible(ev.time)
        else:
            self.queue.append(c2)
            self.queue_counter += 1
            if len(self.queue) > self.max_queue_len:
                self.max_queue_len = len(self.queue)

        self._maybe_open_or_close()

# ============================ Interfaz Tkinter ===========================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Simulación Biblioteca (Tkinter) — Vector de Estado")
        self.geometry("1700x820")

        self.sim: Optional[LibrarySimulation] = None
        self.after_id = None

        self._build_widgets()

    def _build_widgets(self):
        frm_top = ttk.Frame(self)
        frm_top.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(frm_top, text="Tiempo a simular (min o HH:MM):").pack(side=tk.LEFT)
        self.ent_time = ttk.Entry(frm_top, width=12)
        self.ent_time.insert(0, "480")  # default 8h
        self.ent_time.pack(side=tk.LEFT, padx=6)

        self.btn_start = ttk.Button(frm_top, text="Iniciar simulación", command=self.start_sim)
        self.btn_start.pack(side=tk.LEFT, padx=6)

        self.btn_stop = ttk.Button(frm_top, text="Detener", command=self.stop_sim, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        self.lbl_status = ttk.Label(frm_top, text="Estado: listo")
        self.lbl_status.pack(side=tk.LEFT, padx=12)

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        header_canvas = tk.Canvas(outer, height=40)
        header_canvas.pack(side=tk.TOP, fill=tk.X)

        header_frame = ttk.Frame(header_canvas)
        header_canvas.create_window((0,0), window=header_frame, anchor='nw')

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill=tk.BOTH, expand=True)

        xscroll = ttk.Scrollbar(table_frame, orient='horizontal')
        yscroll = ttk.Scrollbar(table_frame, orient='vertical')

        self.tree = ttk.Treeview(table_frame, columns=LibrarySimulation.COLUMNS, show="headings",
                                 xscrollcommand=xscroll.set, yscrollcommand=yscroll.set, height=24)
        xscroll.config(command=self.tree.xview)
        yscroll.config(command=self.tree.yview)

        for col in LibrarySimulation.COLUMNS:
            self.tree.heading(col, text=col)
            width = 120
            if col in ("Evento","Reloj","Hora"):
                width = 90
            if "Demora" in col or "Tiempo" in col or "Prom" in col or "ACUM" in col:
                width = 120
            if col in ("Tipo Transaccion","Lugar"):
                width = 150
            self.tree.column(col, width=width, anchor=tk.CENTER, stretch=False)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Encabezados agrupados
        groups = [
            ("LLEGADA CLIENTE", 5),
            ("COLA", 2),
            ("Transacción", 3),
            ("BIBLIOTECARIOS 1", 3),
            ("BIBLIOTECARIOS 2", 3),
            ("Estadísticas", 4),
            ("CLIENTE", 4),
            ("¿Dónde Lee?", 5)
        ]

        col_widths = [self.tree.column(c, option="width") for c in LibrarySimulation.COLUMNS]
        start_idx = 0
        for title, span in groups:
            span_width = sum(col_widths[start_idx:start_idx+span])
            lbl = ttk.Label(header_frame, text=title, anchor="center")
            lbl.place(x=sum(col_widths[:start_idx]), y=5, width=span_width, height=30)
            start_idx += span

        def sync_headers(*args):
            self.tree.xview(*args)
            if args and args[0] == 'moveto':
                fraction = float(args[1])
                header_canvas.xview_moveto(fraction)
            elif args and args[0] == 'scroll':
                header_canvas.xview_scroll(int(args[1]), args[2])

        xscroll.config(command=sync_headers)
        header_canvas.configure(xscrollcommand=xscroll.set, scrollregion=(0,0,sum(col_widths),40))

    def start_sim(self):
        txt = self.ent_time.get().strip()
        try:
            sim_time = hhmm_to_minutes(txt)
            if sim_time <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror("Error", "Ingrese un tiempo válido en minutos o HH:MM")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)

        self.sim = LibrarySimulation(sim_time_limit=sim_time)
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.lbl_status.config(text="Estado: simulando...")

        self._run_stepwise()

    def _run_stepwise(self):
        if self.sim is None:
            return

        finished = False
        for _ in range(500):
            row, done = self.sim.step()
            if row is not None:
                self.tree.insert("", tk.END, values=row)
            if done:
                finished = True
                break

        self._update_summary_labels()

        if finished:
            self.lbl_status.config(text="Estado: finalizado")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self.after_id = None
        else:
            self.after_id = self.after(1, self._run_stepwise)

    def _update_summary_labels(self):
        if not self.sim:
            return
        self.lbl_status.config(text=f"t={minutes_to_hhmm(self.sim.clock)}  eventos={self.sim.events_processed}  cola={self.sim.queue_counter}")

    def stop_sim(self):
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_status.config(text="Estado: detenido")

if __name__ == "__main__":
    app = App()
    app.mainloop()

import React, { useMemo, useState } from 'react'
import * as XLSX from "xlsx";
import { saveAs } from "file-saver";

const API_URL = '/api/generate'

function isIntegerString(value) {
  return /^-?\d+$/.test(String(value))
}

export default function DistributionForm() {
  const [distribution, setDistribution] = useState('uniforme')
  const [count, setCount] = useState(10)
  const [params, setParams] = useState({ A: 0, B: 1, media: 1, desviacion: 1 })
  const [intervals, setIntervals] = useState(10)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [numbers, setNumbers] = useState([])
  const [histogram, setHistogram] = useState(null)

  const showUniforme = distribution === 'uniforme'
  const showExponencial = distribution === 'exponencial'
  const showNormal = distribution === 'normal'
  const [alpha, setAlpha] = useState(0.05)
  const [gof, setGof] = useState(null)

  const canSubmit = useMemo(() => {
    if (!isIntegerString(count) || Number(count) < 1 || Number(count) > 1000000) return false
    if (showUniforme) {
      const { A, B } = params
      if (A === '' || B === '') return false
      if (Number(A) >= Number(B)) return false
      return true
    }
    if (showExponencial) {
      const { media } = params
      if (media === '' || Number(media) <= 0) return false
      return true
    }
    if (showNormal) {
      const { media, desviacion } = params
      if (media === '' || desviacion === '') return false
      if (Number(desviacion) <= 0) return false
      return true
    }
    return false
  }, [count, params, showUniforme, showExponencial, showNormal])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!canSubmit) return
    setLoading(true)
    setError('')
    setNumbers([])
    setHistogram(null)
    try {
      const payload = {
        distribucion: distribution,
        n: Number(count),
        params: showUniforme
          ? { A: Number(params.A), B: Number(params.B) }
          : showExponencial
          ? { media: Number(params.media) }
          : { media: Number(params.media), desviacion: Number(params.desviacion) },
        k_intervals: Number(intervals)
      }
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      if (!res.ok) {
        const info = await res.json().catch(() => ({}))
        throw new Error(info?.detail || 'Error al generar')
      }
      const data = await res.json()
      setNumbers(data.numbers || [])
      setHistogram(data.histogram || null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(numbers.join('\n'))
    } catch {}
  }

  // === Helpers para ejes "lindos" ===
  const niceMax = (v) => {
    if (v <= 0) return 1
    const pow10 = Math.pow(10, Math.floor(Math.log10(v)))
    const d = v / pow10
    let nice
    if (d <= 1) nice = 1
    else if (d <= 2) nice = 2
    else if (d <= 5) nice = 5
    else nice = 10
    return nice * pow10
  }
  // enteros sin decimales; no enteros con 1 decimal
  const formatTick = (v) => {
    const r = Math.round(v)
    return Math.abs(v - r) < 1e-9 ? String(r) : (Math.round(v * 10) / 10).toFixed(1)
  }

  const handleGoF = async () => {
    if (!histogram || numbers.length === 0) return
    try {
      const payload = {
        distribucion: distribution,
        params: showUniforme
          ? { A: Number(params.A), B: Number(params.B) }
          : showExponencial
          ? { media: Number(params.media) }
          : { media: Number(params.media), desviacion: Number(params.desviacion) },
        n: Number(count),
        edges: histogram.edges,
        observed: histogram.bins.map(b => b.freq),
        alpha: Number(alpha)
      }
      const res = await fetch('/api/gof', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      if (!res.ok) {
        const info = await res.json().catch(() => ({}))
        throw new Error(info?.detail || 'Error en χ²')
      }
      const data = await res.json()
      setGof(data)
    } catch (e) {
      setGof({ error: e.message })
    }
  }

  return (
    <section className="card">
      <form onSubmit={handleSubmit} className="form">
        <div className="form-row">
          <label htmlFor="distribution">Distribución</label>
          <select
            id="distribution"
            value={distribution}
            onChange={(e) => setDistribution(e.target.value)}
          >
            <option value="uniforme">Uniforme [A, B]</option>
            <option value="exponencial">Exponencial (media)</option>
            <option value="normal">Normal (media, desviación)</option>
          </select>
        </div>

        <div className="form-row">
          <label htmlFor="count">Cantidad de números</label>
          <input
            id="count"
            type="number"
            min={1}
            max={1000000}
            step={1}
            inputMode="numeric"
            value={count}
            onChange={(e) => setCount(e.target.value)}
            placeholder="1 .. 1.000.000"
            required
          />
          <small>Solo enteros. Máx. 1.000.000</small>
        </div>

        {/* Intervalos (k) */}
        <div className="form-row">
          <label htmlFor="intervals">Intervalos (k)</label>
          <select id="intervals" value={intervals} onChange={(e) => setIntervals(e.target.value)}>
            {[5, 10, 15, 20, 25].map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </div>

        {showUniforme && (
          <div className="grid-2">
            <div className="form-row">
              <label htmlFor="A">A (límite inferior)</label>
              <input
                id="A"
                type="number"
                value={params.A}
                onChange={(e) => setParams({ ...params, A: e.target.value })}
                required
              />
            </div>
            <div className="form-row">
              <label htmlFor="B">B (límite superior)</label>
              <input
                id="B"
                type="number"
                value={params.B}
                onChange={(e) => setParams({ ...params, B: e.target.value })}
                required
              />
            </div>
            <div className="hint full">
              <small>Requisito: A &lt; B</small>
            </div>
          </div>
        )}

        {showExponencial && (
          <div className="form-row">
            <label htmlFor="media-exp">Media</label>
            <input
              id="media-exp"
              type="number"
              min="0"
              step="any"
              value={params.media}
              onChange={(e) => setParams({ ...params, media: e.target.value })}
              required
            />
            <small>Debe ser &gt; 0</small>
          </div>
        )}

        {showNormal && (
          <div className="grid-2">
            <div className="form-row">
              <label htmlFor="media-norm">Media (μ)</label>
              <input
                id="media-norm"
                type="number"
                step="any"
                value={params.media}
                onChange={(e) => setParams({ ...params, media: e.target.value })}
                required
              />
            </div>
            <div className="form-row">
              <label htmlFor="desv">Desviación (σ)</label>
              <input
                id="desv"
                type="number"
                min="0"
                step="any"
                value={params.desviacion}
                onChange={(e) => setParams({ ...params, desviacion: e.target.value })}
                required
              />
              <small>Debe ser &gt; 0</small>
            </div>
          </div>
        )}

        <div className="actions">
          <button type="submit" disabled={!canSubmit || loading}>
            {loading ? 'Generando…' : 'Generar'}
          </button>
        </div>

        {error && <div className="error">{error}</div>}
      </form>

      <div className="results">
        <div className="results-header">
          <h3>Resultados</h3>
          <div className="results-actions">
            <button onClick={handleCopy} disabled={numbers.length === 0}>Copiar</button>
          </div>
        </div>

        {/* Lista de números */}
        <div className="results-box" role="region" aria-label="Números generados">
          {numbers.length === 0 ? (
            <p className="muted">No hay datos aún.</p>
          ) : (
            <ol>
              {numbers.map((v, i) => <li key={i}><code>{v}</code></li>)}
            </ol>
          )}
        </div>

        {/* Histograma + tabla */}
        {histogram && (
          <div className="results-box" style={{ marginTop: 16, height: 'auto' }}>
            <h4>Histograma de frecuencias (k={histogram.k})</h4>

            {/* === SVG del histograma === */}
            <svg width="100%" viewBox="0 0 860 380" role="img" aria-label={`Histograma con ${histogram.k} intervalos`}>
              <defs>
                <filter id="barShadow" x="-20%" y="-20%" width="140%" height="140%">
                  <feDropShadow dx="0" dy="1" stdDeviation="1.2" floodOpacity="0.25"/>
                </filter>
                <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.95"/>
                  <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.8"/>
                </linearGradient>
              </defs>

              {(() => {
                // Opción A: reservar franja superior para “n · k · fmax”
                const HEADER_H = 24
                const M = { top: 20 + HEADER_H, right: 16, bottom: 96, left: 80 }
                const W = 860, H = 380
                const CW = W - M.left - M.right
                const CH = H - M.top - M.bottom

                const bins = histogram.bins
                const maxF = Math.max(...bins.map(b => b.freq), 1)
                const yMax = niceMax(maxF)
                const yTicks = 5
                const xStep = CW / bins.length
                const gap = 0
                const barW = xStep
                const n = bins.reduce((a, b) => a + b.freq, 0)

                return (
                  <g>
                    {/* Leyenda dentro de la franja reservada */}
                    <text x={M.left} y={M.top - HEADER_H + 16} fontSize="13" fill="var(--muted)">
                      {`n=${n} · k=${histogram.k} · fmax=${maxF}`}
                    </text>

                    {/* Ejes */}
                    <line x1={M.left} y1={M.top} x2={M.left} y2={M.top + CH} stroke="var(--border)" />
                    <line x1={M.left} y1={M.top + CH} x2={M.left + CW} y2={M.top + CH} stroke="var(--border)" />

                    {/* Ticks Y */}
                    {Array.from({ length: yTicks + 1 }, (_, i) => {
                      const v = (yMax / yTicks) * i
                      const y = M.top + CH - (v / yMax) * CH
                      return (
                        <g key={`gy-${i}`}>
                          <line x1={M.left} y1={y} x2={M.left + CW} y2={y} stroke="rgba(255,255,255,0.06)" />
                          <text x={M.left - 8} y={y + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
                            {formatTick(v)}
                          </text>
                        </g>
                      )
                    })}

                    {/* Barras + etiquetas con “clamp” */}
                    {bins.map((b, i) => {
                      const x = M.left + i * xStep + gap / 2
                      const h = (b.freq / yMax) * CH
                      const y = M.top + CH - h

                      // No dejar que el label invada la franja reservada
                      const labelSafe = M.top + 14
                      const topLabel = y - 6
                      const showInside = topLabel < labelSafe
                      const labelY = showInside ? (y + 14) : topLabel
                      const labelFill = showInside ? '#fff' : 'currentColor'

                      return (
                        <g key={i}>
                          <rect
                            x={x} y={y} width={barW} height={h}
                            rx="6" ry="6"
                            fill="url(#barGrad)"
                            filter="url(#barShadow)"
                          >
                            <title>{`Intervalo ${b.label}\nfrecuencia: ${b.freq}`}</title>
                          </rect>
                          <text x={x + barW / 2} y={labelY} textAnchor="middle" fontSize="11" fill={labelFill}>
                            {b.freq}
                          </text>
                        </g>
                      )
                    })}

                    {/* Ticks X */}
                    {bins.map((b, i) => {
                      const xTick = M.left + i * xStep + xStep / 2
                      return (
                        <g key={`gx-${i}`}>
                          <line x1={xTick} y1={M.top + CH} x2={xTick} y2={M.top + CH + 6} stroke="var(--border)" />
                          <text x={xTick} y={M.top + CH + 20} textAnchor="middle" fontSize="11">{b.index}</text>
                          <text
                            x={xTick}
                            y={M.top + CH + 60}
                            textAnchor="middle"
                            fontSize="10"
                            fill="var(--muted)"
                            transform={`rotate(-30 ${xTick} ${M.top + CH + 60})`}
                          >
                            {b.label}
                          </text>
                        </g>
                      )
                    })}

                    {/* Etiquetas de ejes */}
                    <text
                      x={M.left + CW / 2}
                      y={H - 1}
                      textAnchor="middle"
                      fontSize="12"
                      fontWeight="600"
                    >
                      Intervalo [límites]
                    </text>
                    <text
                      x={20}
                      y={M.top + CH / 2}
                      textAnchor="middle"
                      fontSize="12"
                      fontWeight="600"
                      transform={`rotate(-90 20 ${M.top + CH / 2})`}
                    >
                      Frecuencia (f)
                    </text>
                  </g>
                )
              })()}
            </svg>

            {/* Tabla del histograma */}
            <div
              className="results-box"
              style={{ minHeight: 420, overflowY: 'auto', marginTop:12, paddingTop:0 }}
            >
              <table style={{ width: '100%', marginTop: 12, fontSize: 13 }}>
                <thead>
                  <tr>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Numero de intervalo</th>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Intervalo</th>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Frecuencia</th>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>f/n</th>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>F acum</th>
                    <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>F/n acum</th>
                  </tr>
                </thead>
                <tbody>
                  {histogram.bins.map((b) => (
                    <tr key={b.index}>
                      <td style={{ textAlign:'right' }}>{b.index}</td>
                      <td>{b.label}</td>
                      <td style={{ textAlign:'right' }}>{b.freq}</td>
                      <td style={{ textAlign:'right' }}>{b.rel.toFixed(4)}</td>
                      <td style={{ textAlign:'right' }}>{b.cum}</td>
                      <td style={{ textAlign:'right' }}>{b.cum_rel.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="actions">
              <button onClick={() => exportHistogramToExcel(histogram)} disabled={numbers.length === 0 }>
                Descargar tabla Excel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function exportHistogramToExcel(histogram) {
  if (!histogram || !histogram.bins) return;

  const data = histogram.bins.map((b) => ({
    "Número de intervalo": b.index,
    "Intervalo": b.label,
    "Frecuencia": b.freq,
    "f/n": b.rel.toFixed(4),
    "F acum": b.cum,
    "F/n acum": b.cum_rel.toFixed(4),
  }));

  const ws = XLSX.utils.json_to_sheet(data);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Histograma");

  const wbout = XLSX.write(wb, { bookType: "xlsx", type: "array" });
  const blob = new Blob([wbout], { type: "application/octet-stream" });
  saveAs(blob, "histograma.xlsx");
}

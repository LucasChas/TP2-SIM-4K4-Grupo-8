import React, { useMemo, useState } from 'react'
import * as XLSX from "xlsx";
import { saveAs } from "file-saver";

const API_URL = '/api/generate'

function isIntegerString(value) {
  return /^-?\d+$/.test(String(value))
}

// formato: enteros sin decimales; no enteros con 4 decimales
const fmt = (v) => {
  const r = Math.round(Number(v) * 10000) / 10000
  return Number.isInteger(r) ? String(r) : r.toFixed(4)
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

  function isValidNumber(v) {
  if (v === '' || v === null || v === undefined) return false
  const num = Number(v)
  return !isNaN(num) && isFinite(num)
}


  const canSubmit = useMemo(() => {
    if (!isIntegerString(count) || Number(count) < 1 || Number(count) > 1000000) return false
    if (showUniforme) {
      const { A, B } = params
      if (!isValidNumber(A) || !isValidNumber(B)) return false
      if (Number(A) >= Number(B)) return false
      return true
    }
    if (showExponencial) {
      const { media } = params
      if (!isValidNumber(media) || Number(media) <= 0) return false
      return true
    }
    if (showNormal) {
      const { media, desviacion } = params
      if (!isValidNumber(media) || !isValidNumber(desviacion)) return false
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

  /// === Helpers para ejes "lindos" ===
const niceMax = (v) => {
  if (v <= 0) return 1
  const pow10 = Math.pow(10, Math.floor(Math.log10(v)))
  const d = v / pow10
  let nice
  if (d <= 1)      nice = 1
  else if (d <= 2) nice = 2
   // <-- NUEVO: peldaño 2.5
  else if (d <= 5) nice = 4
  else             nice = 8
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
      // Para exponencial: primer límite inferior = 0
      const edgesForGof = [...histogram.edges]
      if (distribution === 'exponencial') edgesForGof[0] = 0

      const payload = {
        distribucion: distribution,
        params: showUniforme
          ? { A: Number(params.A), B: Number(params.B) }
          : showExponencial
          ? { media: Number(params.media) }
          : { media: Number(params.media), desviacion: Number(params.desviacion) },
        n: Number(count),
        edges: edgesForGof,
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
              type="text"
              inputMode="decimal"
              value={params.A?.toString().replace('.', ',')}
              onChange={(e) => setParams({ ...params, A: e.target.value.replace(',', '.') })}
              required
            />
          </div>
          <div className="form-row">
            <label htmlFor="B">B (límite superior)</label>
            <input
              id="B"
              type="text"
              inputMode="decimal"
              value={params.B?.toString().replace('.', ',')}
              onChange={(e) => setParams({ ...params, B: e.target.value.replace(',', '.') })}
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
            type="text"
            inputMode="decimal"
            min="0"
            step="any"
            value={params.media?.toString().replace('.', ',')}
            onChange={(e) => setParams({ ...params, media: e.target.value.replace(',', '.') })}
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
              type="text"
              inputMode="decimal"
              step="any"
              value={params.media?.toString().replace('.', ',')}
              onChange={(e) => setParams({ ...params, media: e.target.value.replace(',', '.') })}
              required
            />
          </div>
          <div className="form-row">
            <label htmlFor="desv">Desviación (σ)</label>
            <input
              id="desv"
              type="text"
              inputMode="decimal"
              min="0"
              step="any"
              value={params.desviacion?.toString().replace('.', ',')}
              onChange={(e) => setParams({ ...params, desviacion: e.target.value.replace(',', '.') })}
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
              const HEADER_H = 24
              const M = { top: 20 + HEADER_H, right: 16, bottom: 96, left: 80 }
              const W = 860, H = 380
              const CW = W - M.left - M.right
              const CH = H - M.top - M.bottom

              const bins = histogram.bins
              const maxF = Math.max(...bins.map(b => b.freq), 1)
              const yMax = niceMax(maxF)
              const yTicks = 5
              const n = bins.reduce((a, b) => a + b.freq, 0)

              // --- “Aire” lateral (content area) ---
              const PADDING_RATIO = 0.06 // ~6% por lado
              const contentLeft   = M.left + CW * PADDING_RATIO
              const contentWidth  = CW * (1 - 2 * PADDING_RATIO)

              // etiquetas de intervalos (ajustando 0 en el primer borde si es exponencial)
              const edgesForViz = (() => {
                const e = [...histogram.edges]
                if (distribution === 'exponencial') e[0] = 0
                return e
              })()
              const labelFor = (i) => `[${fmt(edgesForViz[i])}, ${fmt(edgesForViz[i + 1])}]`

              // === NUEVO: rango real de datos + margen y ticks de X ===
              const dataMin = numbers.length
                ? Math.min(...numbers)
                : edgesForViz[0]
              const dataMax = numbers.length
                ? Math.max(...numbers)
                : edgesForViz[edgesForViz.length - 1]

              const span = edgesForViz[edgesForViz.length - 1] - edgesForViz[0]
              const pad = span * 0.1 || 1

              let xMin = edgesForViz[0] - pad
              let xMax = edgesForViz[edgesForViz.length - 1] + pad

              // forzar a incluir el 0 en el rango
              if (xMin > 0) xMin = 0
              if (xMax < 0) xMax = 0


              // paso "lindo" (…, 0.1, 0.2, 0.5, 1, 2, 5, 10, …)
              const niceTickStep = (rng) => {
                const p = Math.pow(10, Math.floor(Math.log10(rng)))
                const d = rng / p
                let u
                if (d <= 1) u = 0.1
                else if (d <= 2) u = 0.2
                else if (d <= 5) u = 0.5
                else u = 1
                return u * p
              }
              const tickStep = niceTickStep(xMax - xMin)

              // mapeo valor real -> X dentro del “content area”
              // mapeo valor real -> X en el área de contenido usando xMin/xMax (no edges)
              const xFromValue = (v) =>
                contentLeft + ((v - xMin) / (xMax - xMin)) * contentWidth


              // eje Y: si el rango cruza 0 => eje en x=0; si no => borde izq. del content
              const crossesZero = xMin < 0 && xMax > 0
              const yAxisX = xFromValue(0)

              // ticks de X (numéricos, no por intervalos)
              const xTicks = []
              const startTick = Math.ceil(xMin / tickStep) * tickStep
              for (let t = startTick; t <= xMax + 1e-9; t += tickStep) {
                xTicks.push(Number(t.toFixed(10))) // evita ruido flotante
              }

              // escalas X (en content area)
              const xStep = contentWidth / bins.length
              const gap   = 0
              const barW  = xStep - gap

              return (
                <g>
                  {/* Leyenda */}
                  <text x={M.left} y={M.top - HEADER_H + 16} fontSize="13" fill="var(--muted)">
                    {`n=${n} · k=${histogram.k} · fmax=${maxF}`}
                  </text>
                  
                  
                  {/* X solo bajo el área de contenido */}
                  <line
                  x1={xFromValue(xMin)} y1={M.top + CH}
                  x2={xFromValue(xMax)} y2={M.top + CH}
                  stroke="var(--border)" strokeWidth="1.5"
                />

                  {/* Si cruza 0: guía marcada y rótulo "0" */}
                  {crossesZero && (
                    <>
                      <line
                        x1={yAxisX} y1={M.top} x2={yAxisX} y2={M.top + CH}
                        stroke="rgba(255,255,255,0.18)" strokeWidth="3" strokeDasharray="4 6"
                      />
                    
                    </>
                  )}

                  {/* Gridlines y ticks Y */}
                  {Array.from({ length: yTicks + 1 }, (_, i) => {
                    const v = (yMax / yTicks) * i
                    const y = M.top + CH - (v / yMax) * CH
                    return (
                      <g key={`gy-${i}`}>
                        <line
                          x1={contentLeft} y1={y} x2={contentLeft + contentWidth} y2={y}
                          stroke="rgba(255,255,255,0.10)"
                        />
                      </g>
                    )
                  })}

                  {/* Barras */}
                  {bins.map((b, i) => {
                    const x0 = xFromValue(edgesForViz[i])
                    const x1 = xFromValue(edgesForViz[i + 1])
                    const barW = x1 - x0
                    const x = x0
                    const h = (b.freq / yMax) * CH
                    const y = M.top + CH - h

                    const labelSafe = M.top + 14
                    const topLabel  = y - 6
                    const showInside = topLabel < labelSafe
                    const labelY = showInside ? (y + 14) : topLabel
                    const labelFill = showInside ? '#fff' : 'currentColor'

                    return (
                      <g key={i}>
                        <rect
                          x={x} y={y} width={barW} height={h}
                          rx="6" ry="6"
                          fill="url(#barGrad)" filter="url(#barShadow)"
                        >
                          <title>{`Intervalo ${b.index} ${labelFor(i)}\nfrecuencia: ${b.freq}`}</title>
                        </rect>
                        <text
                          x={x + barW / 2} y={labelY}
                          textAnchor="middle" fontSize="11" fill={labelFill}
                        >
                          {b.freq}
                        </text>
                      </g>
                    )
                  })}

                  {/* Eje Y y marca de 0 SOBRE las barras */}
                    <g className="axes-top" pointerEvents="none">
                        <line
                          x1={yAxisX} y1={M.top}
                          x2={yAxisX} y2={M.top + CH}
                          stroke="var(--border)" strokeWidth="2.5"
                        />
                        {crossesZero && (
                          <>
                            <line
                              x1={yAxisX} y1={M.top}
                              x2={yAxisX} y2={M.top + CH}
                              stroke="rgba(255,255,255,0.18)"
                              strokeWidth="3" strokeDasharray="4 6"
                            />
                            
                          </>
                        )}

                {/* NUEVO: labels de Y por encima del histograma */}
                {Array.from({ length: yTicks + 1 }, (_, i) => {
                  if (i === 0) return null;            // evitamos duplicar el "0" (ya lo marcamos abajo)
                  const v = (yMax / yTicks) * i
                  const y = M.top + CH - (v / yMax) * CH
                  return (
                    <text
                      key={`yTop-${i}`}
                      x={yAxisX - 10}
                      y={y + 4}
                      textAnchor="end"
                      fontSize="12"
                      className="yTick"
                    >
                      {formatTick(v)}
                    </text>
                  )
                })}
              </g>
                  {/* Ticks/labels X (solo intervalos; sin 1..k) */}
                  {bins.map((b, i) => {
                    const x0 = xFromValue(edgesForViz[i])
                    const x1 = xFromValue(edgesForViz[i + 1])
                    const xTick = (x0 + x1) / 2  // centro del intervalo real
                    return (
                      <g key={`gx-${i}`}>
                        <text
                          x={xTick}
                          y={M.top + CH + 60}
                          textAnchor="middle"
                          fontSize="10"
                          fill="var(--muted)"
                          transform={`rotate(-30 ${xTick} ${M.top + CH + 60})`}
                        >
                          {labelFor(i)}
                        </text>
                      </g>
                    )
                  })}

                  {/* Ticks/labels X adicionales (escala numérica global) */}
                  {xTicks.map((v, i) => {
                    const xTick = xFromValue(v)
                    return (
                      <g key={`gx-num-${i}`}>
                        <line
                          x1={xTick} y1={M.top + CH}
                          x2={xTick} y2={M.top + CH + 10} // un poquito más largos para distinguirlos
                          stroke="var(--border)"
                        />
                        <text
                          x={xTick}
                          y={M.top + CH + 25}
                          textAnchor="middle"
                          fontSize="11"
                          fontWeight="600"
                          fill="var(--muted)"
                        >
                          {formatTick(v)}
                        </text>
                      </g>
                    )
                  })}

                  {/* Etiquetas de ejes */}
                  <text
                    x={contentLeft + contentWidth / 2}
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

          {/* ===================== TABLA SIMPLE PEDIDA ===================== */}
          <div
            className="results-box"
            style={{ minHeight: 320, overflowY: 'auto', marginTop:12, paddingTop:0 }}
          >
            <table style={{ width: '100%', marginTop: 12, fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Intervalo Numero</th>
                  <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Limite Inferior</th>
                  <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Limite superior</th>
                  <th style={{ position: 'sticky', top: 0, zIndex: 2 }}>Frecuencia observada</th>
                </tr>
              </thead>
              <tbody>
                {histogram.bins.map((b, i) => {
                  // Para exponencial: el 1er límite inferior debe iniciar en 0
                  const lower = (distribution === 'exponencial' && i === 0)
                    ? 0
                    : histogram.edges[i]
                  const upper = histogram.edges[i + 1]
                  return (
                    <tr key={b.index}>
                      <td style={{ textAlign:'right' }}>{b.index}</td>
                      <td style={{ textAlign:'right' }}>{fmt(lower)}</td>
                      <td style={{ textAlign:'right' }}>{fmt(upper)}</td>
                      <td style={{ textAlign:'right' }}>{b.freq}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="actions">
            <button onClick={() => exportHistogramToExcel(histogram, distribution)} disabled={numbers.length === 0}>
              Descargar tabla Excel
            </button>
            {/* <button onClick={handleGoF} disabled={numbers.length === 0}>Calcular χ²</button> */}
          </div>

          {/* Resultado χ² (opcional) */}
          {gof && (
            <div className="results-box" style={{ marginTop: 16 }}>
              {gof.error ? (
                <div className="error">{gof.error}</div>
              ) : (
                <div className="gof">
                  <h4>Bondad de ajuste χ²</h4>
                  <p>χ² observado: <strong>{gof?.chi2?.toFixed?.(4) ?? gof.chi2}</strong></p>
                  <p>χ² crítico (α={alpha}): <strong>{gof?.critical?.toFixed?.(4) ?? gof.critical}</strong></p>
                  <p>Grados de libertad: <strong>{gof?.df}</strong></p>
                  <p>Decisión: <strong>{gof?.reject ? 'Rechazar H₀' : 'No rechazar H₀'}</strong></p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  </section>
)

}

function exportHistogramToExcel(histogram, distribution) {
  if (!histogram || !histogram.bins) return;

  const data = histogram.bins.map((b, i) => {
    const lower = (distribution === 'exponencial' && i === 0)
      ? 0
      : histogram.edges[i]
    const upper = histogram.edges[i + 1]

    const fmtCell = (v) => {
      const r = Math.round(Number(v) * 10000) / 10000
      const str = Number.isInteger(r) ? String(r) : r.toFixed(4)
      return str.replace('.', ',') // <-- cambio clave
    }

    return {
      "Intervalo Numero": b.index,
      "Limite Inferior": fmtCell(lower),
      "Limite superior": fmtCell(upper),
      "Frecuencia observada": b.freq,
    }
  });

  const ws = XLSX.utils.json_to_sheet(data);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Histograma");

  const wbout = XLSX.write(wb, { bookType: "xlsx", type: "array" });
  const blob = new Blob([wbout], { type: "application/octet-stream" });
  saveAs(blob, "Grupo8-Tabla.xlsx");
}


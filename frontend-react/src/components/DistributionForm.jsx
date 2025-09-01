import React, { useMemo, useState } from 'react'

const API_URL = '/api/generate'

function isIntegerString(value) {
  return /^-?\d+$/.test(String(value))
}

function toCSV(numbers) {
  return numbers.join('\n')
}

export default function DistributionForm() {
  const [distribution, setDistribution] = useState('uniforme')
  const [count, setCount] = useState(10)
  const [params, setParams] = useState({ A: 0, B: 1, media: 1, desviacion: 1 })
  const [intervals, setIntervals] = useState(10) // NUEVO
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [numbers, setNumbers] = useState([])
  const [histogram, setHistogram] = useState(null) // NUEVO

  const showUniforme = distribution === 'uniforme'
  const showExponencial = distribution === 'exponencial'
  const showNormal = distribution === 'normal'

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
        k_intervals: Number(intervals) // NUEVO
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
      setHistogram(data.histogram || null) // NUEVO
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

        {/* NUEVO: selector de intervalos */}
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
        <div className="results-box" role="region" aria-label="Números generados">
          {numbers.length === 0 ? (
            <p className="muted">No hay datos aún.</p>
          ) : (
            <ol>
              {numbers.map((v, i) => <li key={i}><code>{v}</code></li>)}
            </ol>
          )}
        </div>

        {/* NUEVO: histograma y tabla */}
        {histogram && (
          <div className="results-box" style={{ marginTop: '16px', height: 'auto' }}>
            <h4>Histograma de frecuencias (k={histogram.k})</h4>
            <svg width="100%" height="260">
              {histogram.bins.map((b, i) => {
                const barW = 100 / histogram.bins.length
                const maxF = Math.max(...histogram.bins.map(x => x.freq))
                const barH = (b.freq / maxF) * 200
                return (
                  <g key={i}>
                    <rect
                      x={`${i * barW}%`}
                      y={220 - barH}
                      width={`${barW - 1}%`}
                      height={barH}
                      fill="#22d3ee"
                    />
                    <text x={`${i * barW + barW / 2}%`} y={240} fontSize="10" textAnchor="middle">
                      {b.index}
                    </text>
                  </g>
                )
              })}
            </svg>
            <table style={{ width: '100%', marginTop: '12px', fontSize: '13px' }}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Intervalo</th>
                  <th>f</th>
                  <th>f/n</th>
                  <th>F acum</th>
                  <th>F/n acum</th>
                </tr>
              </thead>
              <tbody>
                {histogram.bins.map((b) => (
                  <tr key={b.index}>
                    <td>{b.index}</td>
                    <td>{b.label}</td>
                    <td>{b.freq}</td>
                    <td>{b.rel.toFixed(4)}</td>
                    <td>{b.cum}</td>
                    <td>{b.cum_rel.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}

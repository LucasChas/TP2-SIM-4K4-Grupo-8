import React, { useMemo, useState } from 'react'
import * as XLSX from "xlsx";
import { saveAs } from "file-saver";
import { FixedSizeList as List } from 'react-window'
import InfiniteLoader from 'react-window-infinite-loader';

const API_URL = '/api/generate';

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
  const [exporting, setExporting] = useState(false) // 👈 NUEVO
  const [error, setError] = useState('')
  const [numbers, setNumbers] = useState([])
  const [histogram, setHistogram] = useState(null)
  const [isNextPageLoading, setIsNextPageLoading] = useState(false);
  const [hasNextPage, setHasNextPage] = useState(false);

  const showUniforme = distribution === 'uniforme'
  const showExponencial = distribution === 'exponencial'
  const showNormal = distribution === 'normal'
  const [alpha, setAlpha] = useState(0.05)
  const [gof, setGof] = useState(null)

  // === Helpers de validación ===
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

  // === Carga incremental para la lista virtualizada ===
  const loadMoreItems = async (startIndex, stopIndex) => {
    if (isNextPageLoading || !hasNextPage) {
      return;
    }
    setIsNextPageLoading(true);

    try {
      const payload = {
        distribucion: distribution,
        n: Number(count),
        seed: null,
        params: showUniforme
          ? { A: Number(params.A), B: Number(params.B) }
          : showExponencial
          ? { media: Number(params.media) }
          : { media: Number(params.media), desviacion: Number(params.desviacion) },
        skip: startIndex,
        limit: 1000
      };

      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();

      setNumbers(prevNumbers => {
        const newNumbers = [...prevNumbers, ...data.numbers];
        if (newNumbers.length >= Number(count)) {
          setHasNextPage(false);
        }
        return newNumbers;
      });

    } catch (err) {
      setError("Error al cargar más datos. Revisa la consola.");
      console.error("Error al cargar más elementos:", err);
    } finally {
      setIsNextPageLoading(false);
    }
  };

  // === Submit: genera primeros números + histograma ===
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError('');
    setNumbers([]);
    setHistogram(null);
    setHasNextPage(true);

    const HISTOGRAM_API_URL = '/api/histogram';

    try {
      // Base payload con los parámetros comunes
      const basePayload = {
        distribucion: distribution,
        n: Number(count),
        seed: null,
        params: showUniforme
          ? { A: Number(params.A), B: Number(params.B) }
          : showExponencial
          ? { media: Number(params.media) }
          : { media: Number(params.media), desviacion: Number(params.desviacion) },
      };

      // Para la lista: primera “página” grande
      const numbersPayload = {
        ...basePayload,
        skip: 0,
        limit: 100000,
      };

      // Para histograma
      const histogramPayload = {
        ...basePayload,
        k_intervals: Number(intervals),
      };

      const [numbersRes, histogramRes] = await Promise.all([
        fetch(API_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(numbersPayload),
        }),
        fetch(HISTOGRAM_API_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(histogramPayload),
        }),
      ]);

      if (!numbersRes.ok || !histogramRes.ok) {
        const numbersInfo = await numbersRes.json().catch(() => ({}));
        const histogramInfo = await histogramRes.json().catch(() => ({}));
        throw new Error(numbersInfo?.detail || histogramInfo?.detail || 'Error al generar los datos.');
      }

      const numbersData = await numbersRes.json();
      const histogramData = await histogramRes.json();

      setNumbers(numbersData.numbers || []);
      setHistogram(histogramData || null);
      setHasNextPage(numbersData.numbers.length === 100000 && Number(count) > 100000);

    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const isItemLoaded = (index) => {
    return index < numbers.length;
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(numbers.join('\n'))
    } catch {}
  }

  // === NUEVO: Exportar TODOS los números a TXT (hasta 1e6) ===
  const exportAllNumbersToTxt = async () => {
    if (!canSubmit) return;
    setExporting(true);
    setError('');

    try {
      const total = Number(count);
      const pageSize = 100000; // tamaño de página para 1e6
      const all = numbers.slice(0, total); // lo que ya tenés
      let offset = all.length;

      const basePayload = () => ({
        distribucion: distribution,
        n: total,
        seed: null,
        params: (distribution === 'uniforme')
          ? { A: Number(params.A), B: Number(params.B) }
          : (distribution === 'exponencial')
            ? { media: Number(params.media) }
            : { media: Number(params.media), desviacion: Number(params.desviacion) },
      });

      while (offset < total) {
        const limit = Math.min(pageSize, total - offset);
        const payload = { ...basePayload(), skip: offset, limit };

        const res = await fetch(API_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          const info = await res.json().catch(() => ({}));
          throw new Error(info?.detail || 'Error al obtener más números para exportar.');
        }

        const data = await res.json();
        if (!data?.numbers?.length) break;

        all.push(...data.numbers);
        offset += data.numbers.length;
      }

      // aseguramos tamaño exacto y armamos el archivo
      const content = all.slice(0, total).map(v => String(v)).join('\n');
      const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
      const filename = `Grupo8-${distribution}-${total}.txt`;
      saveAs(blob, filename);
    } catch (e) {
      setError(e.message || 'Error al exportar a TXT.');
    } finally {
      setExporting(false);
    }
  };

  /// === Helpers para ejes "lindos" ===
  const niceMax = (v) => {
    if (v <= 0) return 1
    const pow10 = Math.pow(10, Math.floor(Math.log10(v)))
    const d = v / pow10
    let nice
    if (d <= 1)      nice = 1
    else if (d <= 2) nice = 2
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
            {/* 👇 NUEVO: exporta TODOS los números a TXT */}
            <button onClick={exportAllNumbersToTxt} disabled={exporting || loading || !canSubmit}>
              {exporting ? 'Exportando…' : 'Exportar TXT (todos)'}
            </button>
          </div>
        </div>

        <div className="results-box" role="region" aria-label="Números generados">
          {numbers.length === 0 ? (
            <p className="muted">No hay datos aún.</p>
          ) : (
            <InfiniteLoader
              isItemLoaded={isItemLoaded}
              itemCount={Number(count)}
              loadMoreItems={loadMoreItems}
            >
              {({ onItemsRendered, ref }) => (
                <List
                  height={400}
                  itemCount={numbers.length}
                  itemSize={24}
                  width="100%"
                  onItemsRendered={onItemsRendered}
                  ref={ref}
                >
                  {({ index, style }) => (
                    <div style={style}>
                      <code>{numbers[index]}</code>
                    </div>
                  )}
                </List>
              )}
            </InfiniteLoader>
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
                const PADDING_RATIO = 0.06
                const contentLeft   = M.left + CW * PADDING_RATIO
                const contentWidth  = CW * (1 - 2 * PADDING_RATIO)

                const edgesForViz = (() => {
                  const e = [...histogram.edges]
                  if (distribution === 'exponencial') e[0] = 0
                  return e
                })()
                const labelFor = (i) => `[${fmt(edgesForViz[i])}, ${fmt(edgesForViz[i + 1])}]`

                // Rango y ticks X
                const dataMin = numbers.length ? Math.min(...numbers) : edgesForViz[0]
                const dataMax = numbers.length ? Math.max(...numbers) : edgesForViz[edgesForViz.length - 1]

                const span = edgesForViz[edgesForViz.length - 1] - edgesForViz[0]
                const pad = span * 0.1 || 1

                let xMin = edgesForViz[0] - pad
                let xMax = edgesForViz[edgesForViz.length - 1] + pad

                if (xMin > 0) xMin = 0
                if (xMax < 0) xMax = 0

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

                const xFromValue = (v) =>
                  contentLeft + ((v - xMin) / (xMax - xMin)) * contentWidth

                const crossesZero = xMin < 0 && xMax > 0
                const yAxisX = xFromValue(0)

                const xTicks = []
                const startTick = Math.ceil(xMin / tickStep) * tickStep
                for (let t = startTick; t <= xMax + 1e-9; t += tickStep) {
                  xTicks.push(Number(t.toFixed(10)))
                }

                const xStep = contentWidth / bins.length
                const gap   = 0
                const barW  = xStep - gap

                return (
                  <g>
                    {/* Leyenda */}
                    <text x={M.left} y={M.top - HEADER_H + 16} fontSize="13" fill="var(--muted)">
                      {`n=${n} · k=${histogram.k} · fmax=${maxF}`}
                    </text>

                    {/* X bajo el área de contenido */}
                    <line
                      x1={xFromValue(xMin)} y1={M.top + CH}
                      x2={xFromValue(xMax)} y2={M.top + CH}
                      stroke="var(--border)" strokeWidth="1.5"
                    />

                    {/* Si cruza 0: guía */}
                    {crossesZero && (
                      <>
                        <line
                          x1={yAxisX} y1={M.top} x2={yAxisX} y2={M.top + CH}
                          stroke="rgba(255,255,255,0.18)" strokeWidth="3" strokeDasharray="4 6"
                        />
                      </>
                    )}

                    {/* Gridlines Y */}
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

                    {/* Eje Y encima */}
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

                      {/* labels Y */}
                      {Array.from({ length: yTicks + 1 }, (_, i) => {
                        if (i === 0) return null;
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

                    {/* Ticks/labels X por intervalo */}
                    {bins.map((b, i) => {
                      const x0 = xFromValue(edgesForViz[i])
                      const x1 = xFromValue(edgesForViz[i + 1])
                      const xTick = (x0 + x1) / 2
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

                    {/* Ticks/labels X numéricos */}
                    {xTicks.map((v, i) => {
                      const xTick = xFromValue(v)
                      return (
                        <g key={`gx-num-${i}`}>
                          <line
                            x1={xTick} y1={M.top + CH}
                            x2={xTick} y2={M.top + CH + 10}
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

            {/* ===================== TABLA SIMPLE ===================== */}
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
            </div>
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
      return str.replace('.', ',')
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

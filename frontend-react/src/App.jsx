import React from 'react'
import DistributionForm from './components/DistributionForm.jsx'

export default function App() {
  return (
    <div className="container">
      <header className="header">
        <h1>SIMULACION TP2 - Grupo 8</h1>
        <p className="subtitle">Generador de números aleatorios (4 decimales)</p>
      </header>
      <main>
        <DistributionForm />
      </main>
      <footer className="footer">
        <small>Backend: <code>POST http://localhost:8000/generate</code></small>
      </footer>
    </div>
  )
}

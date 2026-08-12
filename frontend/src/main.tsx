import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* GitHub Pages serves under /<repo>/, Cloudflare Pages and Vercel at the
        root. Vite sets BASE_URL from its `base` option, so this is correct for
        both without a conditional -- and without it, a subpath deploy renders a
        blank page because no <Route> ever matches. */}
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)

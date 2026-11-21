/**
 * StageState Frontend Entry Point.
 *
 * Mounts the Root `App` component onto `#root` and loads global theater styles.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

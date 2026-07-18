# App Rendimientos — Rulo Financiero

Aplicación en Python para llevar el seguimiento contable del **Rulo Financiero**: una estrategia de rotación de dinero entre 5 billeteras virtuales, aprovechando que cada una calcula su rendimiento diario tomando el saldo en un horario de captura específico.

## Billeteras y reglas del rulo

| Billetera | Hora de captura | Días activos | TNA |
|---|---|---|---|
| UALA | 9:00hs | Lunes a viernes | 18,44% |
| Montemar Pay | 16:00hs | Lunes a viernes | 19% |
| MercadoPago | 17:20hs | Lunes a viernes | 17% |
| Galicia | 22:00hs | Lunes a viernes | 15,7% |
| Nx (Naranja X) | 22:00hs | Fin de semana (sáb-dom) | 18% |

Fórmula de rendimiento diario: `rendimiento = saldo × TNA / 365`.

Las tasas son editables desde la app, ya que las billeteras las ajustan con frecuencia.

## Estado del proyecto

En diseño. Fase 1: interfaz web (Flask + SQLite). Fase 2 (futura): app de escritorio reutilizando la misma lógica de dominio.

## Stack

- Python
- Flask (servidor web)
- SQLite (persistencia)

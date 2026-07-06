\# 🛡️ Límites de Riesgo y Cortocircuitos (Risk Limits)

\*\*Estatus:\*\* Inmutable por Software



\## 1. CONTROL DE PÉRDIDAS POR OPERACIÓN (SINGLE-TRADE RISK)

\* \*\*Tope Máximo por Contrato de Opciones:\*\* Ninguna posición individual de opciones podrá comprometer más del \*\*10% del capital base total ($500 USD de prima máxima por setup)\*\*.

\* \*\*Límite de Pérdida Estricto (Hard Stop Loss):\*\* Salida irrevocable al alcanzar un \*\*20% de pérdida en el valor de la prima del contrato\*\*, o de forma inmediata si ocurre una invalidación de la estructura técnica identificada en el \*Tape Reading\*.



\## 2. CORTOCIRCUITOS DEL PORTAFOLIO (CIRCUIT BREAKERS)

\* \*\*Daily Drawdown Limit (Cortocircuito Diario):\*\* Si el balance neto de la cuenta se contrae un \*\*5%\*\* en un solo día operativo, el sistema bloquea automáticamente la ejecución de nuevas órdenes durante las próximas 24 horas.

\* \*\*Monthly Drawdown Limit (Cortocircuito Mensual):\*\* Si el portafolio sufre un retroceso del \*\*15%\*\* en el mes corriente, el fondo entra en congelamiento preventivo y se suspende toda operación hasta completar una auditoría manual de los archivos logs.




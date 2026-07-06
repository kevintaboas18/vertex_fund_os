\# 🗺️ Mapa de Conexiones de API (API Connection Map)

\*\*Estructura de Flujo de Datos del Sistema Operativo Vertex Fund OS\*\*



Este archivo traza la ruta técnica por la cual viajan los datos entre los módulos analíticos y de portafolio de forma interna.



\[MÓDULO DE INGESTIÓN] ──► Monitorea SEC EDGAR \& Alertas de Volatilidad Institutional │ ▼ \[RESEARCH AGENT] ───────► Procesa plantillas, calcula Scorecard (04\_Scoring) │ ▼ \[PORTFOLIO AGENT] ──────► Lee 'investor\_constraints.md' y 'risk\_limits.md' │ Calcula Position Sizing basándose en capital de $5,000 USD ▼ \[RISK AGENT] ───────────► Genera 'trade\_ticket\_template.md' con Stop Loss automático del 20% │ ▼ \[TERMINAL LOCAL] ───────► Se congela en el Gate. Solicita firma de 'human\_approval\_checklist.md'



\* \*\*Nota de Conexión Externa:\*\* El pipeline de scripts locales consumirá APIs de mercado para flujos de opciones en tiempo real, guardando los logs estructurados directamente en el sistema de archivos Markdown.




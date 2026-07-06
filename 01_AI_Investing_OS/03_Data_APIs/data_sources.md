\# data\_sources.md



\# 📡 DATA SOURCES \& MARKET FEEDS

\## Official Data Infrastructure \& Intelligence Sources



Este archivo define y centraliza todas las fuentes de datos utilizadas por el sistema para análisis financiero, trading institucional, flujo de opciones, Tape Reading y monitoreo macroeconómico. El objetivo principal es garantizar que toda decisión del sistema esté basada en información confiable, verificable y actualizada en tiempo real o casi tiempo real.



La IA debe utilizar este archivo como referencia principal para validar la calidad, origen y confiabilidad de los datos analizados antes de generar investigaciones, alertas, tesis de inversión o decisiones operativas. Toda información debe priorizar fuentes oficiales, institucionales o reconocidas dentro de la industria financiera.



Los precios históricos de acciones, volumen, datos OHLC, market cap, indicadores técnicos y comportamiento histórico del mercado deben obtenerse mediante APIs financieras confiables y plataformas institucionales reconocidas. Estas fuentes pueden incluir brokers regulados, feeds profesionales de mercado, plataformas de análisis financiero y proveedores especializados de datos bursátiles.



El flujo en tiempo real de opciones, Tape Reading, Time \& Sales, Level II, order flow y actividad institucional debe provenir de plataformas especializadas capaces de mostrar ejecución real del mercado, actividad del option chain, tamaño de órdenes, acumulación institucional y comportamiento de market makers. La prioridad del sistema es detectar flujo agresivo, anomalías de volumen y posicionamiento institucional antes de que el movimiento completo se refleje en el precio del activo.



El sistema también debe integrar fuentes de datos fundamentales y macroeconómicos para validar el contexto general del mercado. Esto incluye earnings reports, SEC filings, FOMC statements, tasas de interés, inflación, desempleo, GDP, liquidez del mercado y cualquier evento macro relevante que pueda impactar sectores específicos o el flujo institucional.



La IA debe diferenciar entre datos confirmados, estimaciones y rumores de mercado. No debe utilizar información no verificada como base principal para generar tesis de inversión o alertas operativas. Si existen conflictos entre múltiples fuentes, el sistema debe priorizar fuentes oficiales, regulatorias o institucionales con mayor confiabilidad histórica.



\---



\## 📊 Fuentes Principales de Datos



\### Market Data \& Historical Prices

\- Datos OHLC (Open, High, Low, Close)

\- Volumen histórico

\- Market Cap

\- Indicadores técnicos

\- Datos históricos de acciones y ETFs



\### Options Flow \& Tape Reading

\- Option Chain en tiempo real

\- Unusual Options Activity

\- Time \& Sales

\- Level II / Order Book

\- Bid-Ask Flow

\- Delta, Gamma y Open Interest

\- Dark Pool Activity

\- Institutional Order Flow



\### Fundamental Data

\- Earnings Reports

\- SEC Filings (10-K, 10-Q, 8-K)

\- Revenue, EPS y márgenes

\- Guidance corporativa

\- Insider Transactions

\- Institutional Ownership



\### Macroeconomic Data

\- Federal Reserve (FOMC)

\- CPI / Inflación

\- Interest Rates

\- GDP

\- Employment Data

\- Liquidity Conditions

\- Bond Market Data



\### News \& Catalysts

\- Earnings Calls

\- Press Releases

\- Regulatory Announcements

\- M\&A Activity

\- AI / Technology Developments

\- Sector Rotation \& Institutional Sentiment



\---



\## 📌 Reglas de Validación de Datos



\- Priorizar siempre fuentes oficiales o institucionales.

\- Confirmar información crítica utilizando múltiples fuentes cuando sea posible.

\- No generar tesis utilizando rumores no verificados.

\- Diferenciar claramente entre datos históricos, tiempo real y estimaciones futuras.

\- Validar anomalías extremas antes de generar alertas operativas.

\- Monitorear calidad y latencia de los feeds utilizados para trading activo.



\---



\## 🎯 Objetivo del Sistema de Datos



El objetivo principal de esta infraestructura es proporcionar información precisa, rápida y confiable que permita detectar oportunidades institucionales, anomalías de mercado y cambios importantes en flujo de capital antes que el mercado general, manteniendo siempre altos estándares de validación y control de calidad sobre los datos analizados.




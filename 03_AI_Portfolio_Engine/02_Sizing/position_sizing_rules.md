\# 🧮 Reglas de Tamaño de Posición (Position Sizing Rules)

\*\*Lógica Algorítmica de Vertex Sizing Engine\*\*



El tamaño de la asignación de capital se calcula de manera inversa a la incertidumbre, cruzando el puntaje obtenido en el `opportunity\_scorecard.md` del Research Desk con las siguientes reglas matemáticas:



\## 1. MATRIZ DE DIMENSIONAMIENTO DE PRIMAS

\* \*\*Puntaje: 27 a 30 puntos (`🟢 Research priority` - Máxima Convicción):\*\*

&#x20; - \*Asignación:\* 10% del portafolio líquido (\*\*$500 USD\*\* de prima asignada).

&#x20; - \*Estructuración:\* Contratos de opciones con Deltas de \*\*0.70 a 0.90\*\*.

\* \*\*Puntaje: 24 a 26 puntos (`🟢 Research priority` - Alta Convicción):\*\*

&#x20; - \*Asignación:\* 5% a 7% del portafolio líquido (\*\*$250 a $350 USD\*\* de prima asignada).

&#x20; - \*Estructuración:\* Contratos con Deltas de \*\*0.60 a 0.70\*\*.

\* \*\*Puntaje: 18 a 23 puntos (`🟡 Watchlist` - Monitoreo Activo):\*\*

&#x20; - \*Asignación:\* \*\*0%\*\*. No se autoriza el despliegue de ningún dólar. El activo permanece bajo observación pasiva.



\## 2. FACTOR DE PENALIZACIÓN POR FLUIDEZ (LIQUIDITY ADJUSTMENT)

\* Si el spread \*bid-ask\* del contrato seleccionado se encuentra entre el 5% y el 10% (dentro del límite, pero no ideal), el motor de tamaño aplicará una reducción automática del \*\*25% al capital asignado\*\* para contrarrestar el impacto del deslizamiento en la ejecución.






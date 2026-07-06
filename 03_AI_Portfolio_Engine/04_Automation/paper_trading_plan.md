\# 🧪 Plan de Simulación Operativa (Paper Trading Plan)

\*\*Fase Obligatoria de Validación de Software\*\*



Antes de comprometer un solo dólar del capital real de Vertex Holding Group, el ecosistema integrado de agentes se someterá a un protocolo de simulación estricto.



\## 1. REGLAS DEL ENTORNO DE PRUEBA

\* \*\*Duración Mínima:\*\* Un ciclo cerrado de \*\*20 operaciones de opciones\*\* consecutivas registradas en una cuenta de simulación (Paper Trading).

\* \*\*Parámetros del Entorno:\*\* La cuenta de simulación se configurará con un capital ficticio inicial reflejando exactamente la realidad humana: \*\*$5,000 USD\*\*.



\## 2. CRITERIOS DE GRADUACIÓN (PASO A CAPITAL REAL)

El sistema recibirá luz verde para operar en mercados en vivo si y solo si:

\* Se cumple al 100% el límite de stop loss del 20% en todas las posiciones simuladas.

\* El modelo de dimensionamiento calcula correctamente las primas según los scores (ej. nunca exceder $500 USD en scores perfectos).

\* El `decision\_log.md` almacena todos los registros sin errores de formato.






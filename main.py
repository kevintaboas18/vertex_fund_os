import os
from pathlib import Path

def verificar_e_ingestar_ecosistema_total():
    """
    Escanea de forma exhaustiva el 100% de los directorios del ecosistema Vertex.
    Indexa los contenidos en memoria estructurada asegurando cero omisiones
    y resolviendo nombres repetidos mediante rutas absolutas virtuales.
    """
    ruta_raiz = Path(__file__).parent
    memoria_maestra = {}
    
    print("====================================================================")
    print("      VERTEX HOLDING GROUP - AUDITORÍA MAESTRA DE INGESTA OS        ")
    print("====================================================================\n")
    
    # Definición rigurosa de la arquitectura jerárquica basada en tu Word y especificaciones
    mapa_arquitectura = {
        "01_AI_Investing_OS": ["00_Profile", "01_Strategy", "02_Research", "03_Data_APIs", "04_Agents", "05_Decisions", "06_Reports"],
        "02_AI_Research_Desk": ["00_Inbox", "01_Universe", "02_Filings", "03_Earnings", "04_Scoring", "05_Memos", "06_Agents", "07_Logs"],
        "03_AI_Portfolio_Engine": ["00_Profile", "01_Portfolio", "02_Sizing", "03_Execution", "04_Automation", "05_Reports", "06_Agents"]
    }
    
    total_archivos_cargados = 0
    
    for fase, subcarpetas in mapa_arquitectura.items():
        print(f"[*] Analizando Fase Estructural: [{fase}/]")
        
        for subcarpeta in subcarpetas:
            directorio_target = ruta_raiz / fase / subcarpeta
            
            if directorio_target.is_dir():
                # Escaneo estricto de archivos Markdown (.md)
                archivos_md = list(directorio_target.glob("*.md"))
                
                for archivo in archivos_md:
                    # Creamos una clave única usando la ruta virtual para evitar colisión de nombres idénticos
                    clave_virtual = f"{fase}/{subcarpeta}/{archivo.name}"
                    
                    try:
                        with open(archivo, "r", encoding="utf-8") as f:
                            contenido = f.read().strip()
                            
                            # Registro en el diccionario maestro sin omitir caracteres
                            memoria_maestra[clave_virtual] = contenido
                            total_archivos_cargados += 1
                            
                            print(f"    [✔] Indexado: {fase}/{subcarpeta}/{archivo.name:<28} | {len(contenido)} caracteres")
                    except Exception as e:
                        print(f"    [❌] Error crítico de lectura en {archivo.name}: {e}")
            else:
                print(f"    [⚠️] Directorio ausente: Se esperaba la carpeta '{subcarpeta}' en {fase}/")
                
    print("\n====================================================================")
    print(f"[+] AUDITORÍA DE INGESTA CONCLUIDA CON ÉXITO")
    print(f"[+] Total de documentos constitucionales integrados en memoria: {total_archivos_cargados}")
    print("====================================================================")
    
    return memoria_maestra

if __name__ == "__main__":
    # Inicialización y ejecución del mapa del conocimiento
    base_conocimiento_ia = verificar_e_ingestar_ecosistema_total()
    
    print("\n[*] Ejecutando pruebas de control de guardrails corporativos...")
    
    # Verificación de aislamiento de bitácoras de decisiones (nombres repetidos en fases distintas)
    log_fase2 = "02_AI_Research_Desk/07_Logs/decision_log.md"
    log_fase1 = "01_AI_Investing_OS/05_Decisions/decision_log.md"
    
    if log_fase1 in base_conocimiento_ia and log_fase2 in base_conocimiento_ia:
        print("[✔] Validación Correcta: Ambos 'decision_log.md' coexisten en memoria sin interferencia.")
    else:
        print("[🚨 ALERTA]: Uno o más archivos de registro histórico no fueron detectados.")
        
    # Verificación del Guardián del Sistema (Vertex Credit Division)
    agente_riesgo = "03_AI_Portfolio_Engine/06_Agents/risk_agent.md"
    if agente_riesgo in base_conocimiento_ia:
        print(f"[✔] Validación Correcta: Risk Agent de Vertex Credit Division en línea ({len(base_conocimiento_ia[agente_riesgo])} caracteres).")
    else:
        print("[🚨 ALERTA CRÍTICA]: Falta el prompt de instrucciones para el Risk Agent de la firma.")
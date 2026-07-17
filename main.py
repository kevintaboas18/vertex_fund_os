import os
from pathlib import Path

def verificar_e_ingestar_ecosistema_total():
    """
    Escanea e indexa la base de conocimiento WBJ (Ruta 2030 Wall Street Agent
    System v2.0.0) que vive en /Cerebro, más el perfil del inversionista y la
    memoria. Registra cada documento en memoria estructurada con clave por
    ruta virtual para evitar colisiones de nombres repetidos entre carpetas.
    """
    ruta_raiz = Path(__file__).parent
    memoria_maestra = {}

    print("====================================================================")
    print("      WARREN BUFFETT JR — AUDITORÍA DE INGESTA (Ruta 2030 v2.0.0)   ")
    print("====================================================================\n")

    # Estructura jerárquica del Cerebro + carpetas de conocimiento del proyecto.
    mapa_arquitectura = {
        "Cerebro/00_main_agent":        None,
        "Cerebro/01_business_analysis": None,
        "Cerebro/02_financial_analysis":None,
        "Cerebro/03_market_analysis":   None,
        "Cerebro/04_technical_momentum":None,
        "Cerebro/05_risk_analysis":     None,
        "Cerebro/06_valuation_analysis":None,
        "Cerebro/shared":               None,
        "Cerebro/special_sauces":       None,
        "Cerebro/examples":             None,
        "Perfil Inversionista":         None,
        "Memoria":                      None,
    }

    total_archivos_cargados = 0

    for fase in mapa_arquitectura:
        directorio_target = ruta_raiz / fase
        print(f"[*] Analizando: [{fase}/]")

        if directorio_target.is_dir():
            archivos_md = sorted(directorio_target.glob("*.md"))
            if not archivos_md:
                print(f"    [·] Sin documentos .md en {fase}/")
            for archivo in archivos_md:
                clave_virtual = f"{fase}/{archivo.name}"
                try:
                    with open(archivo, "r", encoding="utf-8") as f:
                        contenido = f.read().strip()
                        memoria_maestra[clave_virtual] = contenido
                        total_archivos_cargados += 1
                        print(f"    [✔] Indexado: {archivo.name:<34} | {len(contenido)} caracteres")
                except Exception as e:
                    print(f"    [❌] Error crítico de lectura en {archivo.name}: {e}")
        else:
            print(f"    [⚠️] Directorio ausente: se esperaba '{fase}/'")

    print("\n====================================================================")
    print(f"[+] AUDITORÍA DE INGESTA CONCLUIDA")
    print(f"[+] Total de documentos de la base de conocimiento integrados: {total_archivos_cargados}")
    print("====================================================================")

    return memoria_maestra

if __name__ == "__main__":
    base_conocimiento_ia = verificar_e_ingestar_ecosistema_total()

    print("\n[*] Ejecutando validaciones de guardrails del sistema WBJ...")

    # El orquestador (agente principal) debe estar presente.
    main_prompt = "Cerebro/00_main_agent/PROMPT.md"
    scoring = "Cerebro/00_main_agent/SCORING_AND_GATES.md"
    if main_prompt in base_conocimiento_ia and scoring in base_conocimiento_ia:
        print("[✔] Orquestador y motor de scoring/gates en línea.")
    else:
        print("[🚨 ALERTA]: Falta el prompt del orquestador o el motor de scoring/gates.")

    # Los 6 especialistas deben tener su SCORING.md.
    especialistas = ["01_business_analysis", "02_financial_analysis", "03_market_analysis",
                     "04_technical_momentum", "05_risk_analysis", "06_valuation_analysis"]
    faltantes = [e for e in especialistas
                 if f"Cerebro/{e}/SCORING.md" not in base_conocimiento_ia]
    if not faltantes:
        print(f"[✔] Los 6 especialistas tienen su metodología de scoring cargada.")
    else:
        print(f"[🚨 ALERTA CRÍTICA]: Especialistas sin SCORING.md: {faltantes}")

    # El perfil del inversionista debe leerse SIEMPRE antes de recomendar.
    perfil = "Perfil Inversionista/Victor Gonzalez.md"
    if perfil in base_conocimiento_ia:
        print(f"[✔] Perfil del inversionista cargado ({len(base_conocimiento_ia[perfil])} caracteres).")
    else:
        print("[⚠️] Perfil del inversionista (.md) no detectado — revisar carpeta 'Perfil Inversionista'.")

#!/usr/bin/env bash
#
# ¿Está el árbol donde debe, o se ha rebobinado solo?
#
#     bash scripts/guardia_arbol.sh
#
# Existe por una avería del ENTORNO, no del repositorio. El contenedor remoto
# en el que corre el agente es efímero y en la sesión del 22/08/2026 rebobinó
# el disco SEIS veces a un commit viejo, sin avisar y en mitad del trabajo.
#
# Lo que se lleva por delante no es solo el código escrito. Es peor:
#
#   1. Se lee un archivo creyendo que tiene los cambios de hoy y tiene los de
#      hace tres días — y el diagnóstico que sale de ahí es sobre código que
#      ya no existe.
#   2. Se corre la batería creyendo que mide el trabajo nuevo y mide el árbol
#      viejo. Sale VERDE y ese verde no significa nada. Pasó.
#   3. Se commitea encima de un historial rebobinado.
#
# El primero es el que más duele porque no deja rastro: no falla nada, solo se
# razona sobre lo que no es.
#
# `.git` se rebobina TAMBIÉN —comprobado con el reflog: tras una reversión su
# entrada más reciente era de dos días antes—, así que un hook de git no vale:
# desaparece justo cuando hace falta. Lo único que sobrevive es el REMOTO, y
# por eso este guardián pregunta ahí y no al disco.
set -uo pipefail

RAMA="${1:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null)}"
[ -z "$RAMA" ] || [ "$RAMA" = "HEAD" ] && RAMA="claude/ai-agent-review-jjicf9"

if ! git fetch origin "$RAMA" --quiet 2>/dev/null; then
    echo "AVISO: no se pudo preguntar al remoto por '$RAMA'."
    echo "       Sin remoto no hay forma de saber si el árbol está al día."
    echo "       NO se bloquea —una red caída no es una reversión—, pero"
    echo "       tampoco se puede afirmar que lo que hay en disco sea lo bueno."
    exit 2
fi

AQUI="$(git rev-parse HEAD)"
ALLA="$(git rev-parse "origin/$RAMA")"

if [ "$AQUI" = "$ALLA" ]; then
    echo "OK · el árbol está donde debe: ${AQUI:0:8} ($RAMA)"
    exit 0
fi

# ¿Está el disco DETRÁS del remoto? Eso es la reversión: trabajo que ya se
# subió y que aquí ya no está.
if git merge-base --is-ancestor "$AQUI" "$ALLA" 2>/dev/null; then
    PERDIDOS="$(git rev-list --count "$AQUI..$ALLA")"
    echo "PARA. El árbol se ha REBOBINADO."
    echo
    echo "  en el disco: ${AQUI:0:8}"
    echo "  en el remoto: ${ALLA:0:8}   ($PERDIDOS commit(s) por delante)"
    echo
    echo "  Lo que haya en disco es de ANTES de ese trabajo. No leas este"
    echo "  código para diagnosticar, no te fíes de una batería que haya"
    echo "  corrido sobre él, y no commitees encima."
    echo
    echo "  Se arregla con:"
    echo "    git fetch origin $RAMA && git reset --hard origin/$RAMA"
    exit 1
fi

# Por delante del remoto es lo normal a mitad de trabajo: hay commits sin subir.
if git merge-base --is-ancestor "$ALLA" "$AQUI" 2>/dev/null; then
    echo "OK · ${AQUI:0:8}, con $(git rev-list --count "$ALLA..$AQUI") commit(s) sin subir."
    exit 0
fi

echo "PARA. El disco y el remoto han DIVERGIDO."
echo "  en el disco: ${AQUI:0:8}   ·   en el remoto: ${ALLA:0:8}"
echo "  Ninguno contiene al otro: hay que mirar qué pasó antes de tocar nada."
exit 1

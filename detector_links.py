import re
from urllib.parse import urlparse


# ============================================================
# LISTAS DE REFERENCIA
# ============================================================

# Dominios acortadores conocidos
ACORTADORES = {
    "bit.ly", "tinyurl.com", "t.co", "cutt.ly", "goo.gl", "rebrand.ly",
    "ow.ly", "short.link", "tiny.cc", "is.gd", "buff.ly", "ift.tt",
    "shorte.st", "adf.ly", "bc.vc", "shorten.asia", "clck.ru",
    "qr.ae", "mcaf.ee", "su.pr", "snip.ly", "tr.im", "url4.eu",
}

# Dominios legítimos conocidos (no se analizan como sospechosos)
DOMINIOS_CONFIABLES = {
    "google.com", "gmail.com", "youtube.com", "microsoft.com",
    "office.com", "outlook.com", "live.com", "hotmail.com",
    "apple.com", "icloud.com", "amazon.com", "paypal.com",
    "linkedin.com", "github.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "whatsapp.com",
    "netflix.com", "spotify.com", "dropbox.com", "zoom.us",
    "slack.com", "notion.so", "trello.com", "atlassian.com",
    "mercadolibre.com", "mercadopago.com", "visa.com", "mastercard.com",
}

# Palabras en la URL que son señal de alerta
PALABRAS_PELIGROSAS = [
    "login", "signin", "verify", "secure", "account", "update",
    "confirm", "password", "credential", "bank", "wallet",
    "suspended", "urgent", "alert", "validate", "authenticate",
    "webscr", "cmd=_s-xclick", "phish", "recover", "unlock",
    "acceso", "verificar", "contraseña", "seguro", "cuenta",
    "suspendido", "urgente", "validar", "recuperar",
]

# Extensiones de archivo ejecutables en URLs (muy sospechoso)
EXTENSIONES_PELIGROSAS = [
    ".exe", ".bat", ".ps1", ".vbs", ".js", ".jar",
    ".scr", ".cmd", ".msi", ".dll", ".zip", ".rar",
]


# ============================================================
# EXTRACTOR DE URLs
# ============================================================

def extraer_urls(texto: str) -> list[str]:
    """Extrae todas las URLs del texto (HTML o texto plano)."""
    if not texto:
        return []

    # Patrón amplio que captura http/https y también URLs sin protocolo
    patron = r'https?://[^\s\'"<>)\]]+|www\.[^\s\'"<>)\]]+'
    urls_raw = re.findall(patron, texto, re.IGNORECASE)

    # Limpiar caracteres basura al final (puntos, comas, paréntesis)
    urls_limpias = []
    for url in urls_raw:
        url = re.sub(r'[.,;:)\]>]+$', '', url)
        if url not in urls_limpias:
            urls_limpias.append(url)

    return urls_limpias


def obtener_dominio(url: str) -> str:
    """Extrae el dominio raíz de una URL."""
    try:
        if not url.startswith("http"):
            url = "http://" + url
        parsed = urlparse(url)
        dominio = parsed.netloc.lower()
        # Eliminar "www."
        dominio = re.sub(r'^www\.', '', dominio)
        return dominio
    except Exception:
        return ""


# ============================================================
# ANÁLISIS DE CADA URL
# ============================================================

def analizar_url(url: str) -> dict:
    """
    Analiza una sola URL y devuelve un diccionario con:
    - url: la URL original
    - dominio: el dominio extraído
    - es_sospechosa: bool
    - razones: lista de strings con los motivos
    - nivel: "CRÍTICO", "ALTO", "MEDIO", "LIMPIA"
    """
    resultado = {
        "url": url,
        "dominio": "",
        "es_sospechosa": False,
        "razones": [],
        "nivel": "LIMPIA"
    }

    dominio = obtener_dominio(url)
    resultado["dominio"] = dominio

    if not dominio:
        resultado["razones"].append("No se pudo parsear la URL")
        resultado["es_sospechosa"] = True
        resultado["nivel"] = "MEDIO"
        return resultado

    # Ignorar dominios confiables conocidos
    dominio_base = ".".join(dominio.split(".")[-2:])  # Ej: "google.com" de "mail.google.com"
    if dominio_base in DOMINIOS_CONFIABLES:
        return resultado

    razones = []

    # 1. ¿Es un acortador?
    if dominio in ACORTADORES or dominio_base in ACORTADORES:
        razones.append(f"Servicio acortador de URLs ({dominio}) — oculta el destino real")

    # 2. ¿Tiene IP en lugar de dominio?
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}(:\d+)?$', dominio):
        razones.append("URL apunta directamente a una dirección IP (técnica de evasión)")

    # 3. ¿El dominio imita a una marca conocida? (typosquatting)
    marcas = ["paypal", "netflix", "microsoft", "google", "amazon",
              "apple", "mercadolibre", "bancogalicia", "santander",
              "bbva", "hsbc", "visa", "mastercard", "instagram", "facebook"]
    for marca in marcas:
        if marca in dominio and dominio_base not in DOMINIOS_CONFIABLES:
            razones.append(f"El dominio imita a '{marca}' (posible typosquatting)")
            break

    # 4. ¿Tiene palabras peligrosas en la URL?
    url_lower = url.lower()
    palabras_encontradas = [p for p in PALABRAS_PELIGROSAS if p in url_lower]
    if palabras_encontradas:
        razones.append(f"Palabras de alerta en la URL: {', '.join(palabras_encontradas)}")

    # 5. ¿Tiene extensión de archivo ejecutable?
    ext_encontradas = [e for e in EXTENSIONES_PELIGROSAS if url_lower.endswith(e)]
    if ext_encontradas:
        razones.append(f"La URL descarga un archivo peligroso: {', '.join(ext_encontradas)}")

    # 6. ¿Subdominio excesivo? (google.com.evil.ru → técnica común)
    partes = dominio.split(".")
    if len(partes) > 4:
        razones.append(f"Dominio con subdominios excesivos ({dominio}) — técnica de camuflaje")

    # 7. ¿Dominio con guiones sospechosos? (secure-paypal-login.com)
    if dominio.count("-") >= 2:
        razones.append(f"Dominio con múltiples guiones ({dominio}) — patrón común en phishing")

    # 8. ¿Protocolo HTTP (no HTTPS)?
    if url.startswith("http://"):
        razones.append("Usa HTTP sin cifrado (no HTTPS)")

    # Calcular nivel según cantidad y tipo de razones
    if razones:
        resultado["es_sospechosa"] = True
        resultado["razones"] = razones

        # Nivel CRÍTICO si tiene acortador + palabras peligrosas, o IP directa, o ejecutable
        critico = (
            any("acortador" in r for r in razones) and any("alerta" in r for r in razones)
            or any("IP" in r for r in razones)
            or any("ejecutable" in r or "descarga" in r for r in razones)
            or any("typosquatting" in r or "imita" in r for r in razones)
        )
        resultado["nivel"] = "CRÍTICO" if critico else "ALTO"
    
    return resultado


# ============================================================
# FUNCIÓN PRINCIPAL (reemplaza a la original)
# ============================================================

def detectar_links_sospechosos(texto: str) -> list[dict]:
    """
    Analiza todas las URLs del texto y devuelve solo las sospechosas.
    
    Retorna una lista de dicts con:
        { url, dominio, nivel, razones }
    
    Compatible con el resto del WorkerMulti: si no hay sospechosos, retorna [].
    """
    if not texto:
        return []

    urls = extraer_urls(texto)
    if not urls:
        return []

    sospechosas = []
    for url in urls:
        analisis = analizar_url(url)
        if analisis["es_sospechosa"]:
            sospechosas.append({
                "url": analisis["url"],
                "dominio": analisis["dominio"],
                "nivel": analisis["nivel"],
                "razones": analisis["razones"]
            })

    return sospechosas


def resumen_links(sospechosas: list[dict]) -> str:
    """
    Convierte la lista de sospechosas en un string legible
    para pasarle al prompt de Groq o al mensaje de Telegram.
    """
    if not sospechosas:
        return "Ninguno"

    lineas = []
    for s in sospechosas:
        razones_str = " | ".join(s["razones"])
        lineas.append(f"[{s['nivel']}] {s['url']} → {razones_str}")

    return "\n".join(lineas)


# ============================================================
# TEST RÁPIDO (ejecutar con: python detector_links.py)
# ============================================================

if __name__ == "__main__":
    texto_prueba = """
    Estimado cliente, su cuenta fue suspendida.
    Verifique sus datos en: http://paypa1-secure-login.com/verify?cmd=update
    También puede entrar por: https://bit.ly/3xAbCd
    O descargue el formulario: http://192.168.1.100/form.exe
    Atentamente, Soporte Técnico.
    
    PD: Si ya lo hizo, ignore este mensaje. Visite https://www.google.com
    """

    resultados = detectar_links_sospechosos(texto_prueba)

    print("=" * 60)
    print(f"URLs sospechosas encontradas: {len(resultados)}")
    print("=" * 60)
    for r in resultados:
        print(f"\n🔗 URL:     {r['url']}")
        print(f"   Dominio: {r['dominio']}")
        print(f"   Nivel:   {r['nivel']}")
        for razon in r['razones']:
            print(f"   ⚠️  {razon}")

    print("\n--- Resumen para Groq/Telegram ---")
    print(resumen_links(resultados))
import os
import time
import httpx
import re
import traceback
import msal
import datetime
import json

from supabase import create_client
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv
from groq import Groq

try:
    from detector_links import detectar_links_sospechosos, resumen_links
except ImportError:
    def detectar_links_sospechosos(texto):
        if not texto: return []
        acortadores = [r'bit\.ly', r'tinyurl\.com', r't\.co', r'cutt\.ly', r'goo\.gl', r'rebrand\.ly']
        return [p for p in acortadores if re.search(p, texto, re.IGNORECASE)]
    def resumen_links(links): return str(links) if links else "Ninguno"

load_dotenv()

# ============================================================
# CONFIGURACIÓN BASE
# ============================================================
MAX_CORREOS_POR_CARPETA = 3
ARCHIVO_HISTORIAL       = "historial_procesados.txt"   # Punto 7: persistencia en disco

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
cipher_suite = Fernet(os.getenv("VIGILANTE_MASTER_KEY").encode())

try:
    cipher_suite2 = Fernet(os.getenv("VIGILANTE_MASTER_KEY2").encode())
except Exception:
    cipher_suite2 = cipher_suite

def descifrar(texto_cifrado: str) -> str:
    return cipher_suite.decrypt(texto_cifrado.encode()).decode()

def descifrar2(texto_cifrado: str) -> str:
    try:
        return cipher_suite2.decrypt(texto_cifrado.encode()).decode()
    except Exception:
        return descifrar(texto_cifrado)

# Claves descifradas al arrancar
GROQ_API_KEY = descifrar2("TOKEN AQUI ENCRIPTADO ")
TELEGRAM_TOKEN_DEFAULT = descifrar("TOKEN AQUI ENCRIPTADO")
mi_chat_id = "TOKEN AQUI"
mi_bot_token = "TOKEN AQUI"

# Punto 1 corregido: token del admin viene del .env, no hardcodeado
TELEGRAM_ADMIN_CHAT_ID = str(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "1420090901"))

msal_app = msal.ConfidentialClientApplication(
    os.getenv("MICROSOFT_CLIENT_ID"),
    authority="https://login.microsoftonline.com/consumers",
    client_credential=os.getenv("MICROSOFT_CLIENT_SECRET")
)

HISTORIAL_IDS = set()
MESES_IMAP    = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ============================================================
# PUNTO 7: HISTORIAL PERSISTENTE EN DISCO
# ============================================================

def cargar_historial_desde_disco() -> set:
    """
    Lee el archivo TXT, carga los IDs en memoria y trunca entradas
    de más de 2 días para evitar crecimiento infinito del archivo.
    """
    ids = set()
    if not os.path.exists(ARCHIVO_HISTORIAL):
        print(f"📂 Historial nuevo — sin IDs previos.")
        return ids

    with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
        lineas = [l.strip() for l in f if l.strip()]

    # Mantener solo las últimas 5000 líneas (aprox. 2 días con 3 buzones)
    MAX_LINEAS = 5000
    if len(lineas) > MAX_LINEAS:
        lineas = lineas[-MAX_LINEAS:]
        # Reescribir el archivo truncado
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        print(f"📂 Historial truncado a {MAX_LINEAS} entradas.")

    ids = set(lineas)
    print(f"📂 Historial cargado desde disco: {len(ids)} IDs previos.")
    return ids

def guardar_id_en_disco(msg_id: str):
    """Persiste un ID en el archivo para sobrevivir reinicios."""
    with open(ARCHIVO_HISTORIAL, "a", encoding="utf-8") as f:
        f.write(f"{msg_id}\n")


# ============================================================
# HELPERS
# ============================================================

def es_token_valido(valor) -> bool:
    if not valor: return False
    valor = str(valor).strip()   # ← convierte int/None a string
    if valor.startswith("@"): return False
    if ":" not in valor: return False
    return valor.split(":")[0].isdigit()

def es_chat_id_valido(valor) -> bool:
    if not valor: return False
    try:
        int(str(valor).strip())  # ← ya estaba bien, pero reforzamos
        return True
    except (ValueError, TypeError):
        return False

def get_status_icon(resultado: str) -> str:
    if "CRÍTICO" in resultado: return "🚨"
    if "ALTO"    in resultado: return "⚠️"
    if "MEDIO"   in resultado: return "🟡"
    return "🟢"


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(chat_id_raw, token_raw, mensaje):
    """Envía un mensaje a Telegram con validación completa."""
    if es_token_valido(token_raw):
        token = token_raw.strip()
        fuente = "bot del cliente"
    else:
        token = TELEGRAM_TOKEN_DEFAULT.strip()
        fuente = "bot global"
        if token_raw:
            print(f"   ⚠️  Token '{token_raw}' inválido → usando bot global.")

    if not es_chat_id_valido(chat_id_raw):
        print(f"   ❌ Chat ID inválido: '{chat_id_raw}'. Debe ser un número.")
        return False

    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": str(chat_id_raw).strip(), "text": mensaje, "parse_mode": "Markdown"}

    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            print(f"   📲 Alerta enviada ({fuente}).")
            return True
        else:
            err  = resp.json()
            code = err.get("error_code", "?")
            desc = err.get("description", "Sin descripción")
            print(f"   ❌ Telegram rechazó ({code}): {desc}")
            if code == 404:
                print(f"   💡 Token inválido. Verificá en @BotFather → /mybots → API Token.")
            elif "chat not found" in str(desc).lower():
                print(f"   💡 El cliente debe buscar el bot y presionar /start.")
            elif code == 403:
                print(f"   💡 El usuario bloqueó el bot.")
            return False
    except httpx.TimeoutException:
        print(f"   ❌ Timeout conectando con Telegram.")
        return False
    except Exception as e:
        print(f"   ❌ Error de red Telegram: {e}")
        return False


def construir_alerta(icon, ubicacion, email_cuenta, remitente, asunto, resultado, links, explicacion_ia: str = ""):
    """Mensaje de alerta inmediata por amenaza detectada."""
    ahora     = datetime.datetime.now().strftime("%H:%M:%S")
    links_str = resumen_links(links) if links else "Ninguno"
    if len(links_str) > 300:
        links_str = links_str[:297] + "..."

    # Bloque de explicación IA — solo si hay contenido
    ia_str = f"\n🤖 *Qué significa esto:*\n_{explicacion_ia}_\n" if explicacion_ia else ""

    return (
        f"{icon} *ALERTA DE SEGURIDAD — Vigilante SOC*\n\n"
        f"📍 *Carpeta:* `{ubicacion}`\n"
        f"👤 *Buzón:* `{email_cuenta}`\n"
        f"📧 *De:* {remitente[:80]}\n"
        f"📝 *Asunto:* {asunto[:80]}\n"
        f"⚖️ *Veredicto IA:* `{resultado}`\n"
        f"🔗 *Links sospechosos:*\n`{links_str}`\n"
        f"⏰ *Hora:* {ahora}\n"
        f"{ia_str}"
    )


# ============================================================
# REPORTES — 3 ENVÍOS DIARIOS
# 06:00 → Parcial mañana
# 14:00 → Parcial tarde
# 22:00 → Completo del día con detalle de amenazas + tip IA
# ============================================================

def _obtener_stats_cliente(cliente_id: str, desde: str) -> tuple[dict, list]:
    """
    Devuelve (stats, registros_detalle) para un cliente desde una fecha ISO.
    registros_detalle incluye remitente, asunto y veredicto para el reporte completo.
    """
    res_buz     = supabase.table("buzones_monitoreados").select("id").eq(
        "cliente_id", cliente_id
    ).execute()
    ids_buzones = [b["id"] for b in (res_buz.data or [])]

    if not ids_buzones:
        return {}, []

    res_am = supabase.table("amenazas_detectadas").select(
        "veredicto_ia, remitente, asunto, fecha_analisis"
    ).in_("buzon_id", ids_buzones).gte("fecha_analisis", desde).execute()

    registros = res_am.data or []
    stats = {
        "total":    len(registros),
        "criticos": sum(1 for r in registros if r["veredicto_ia"] == "CRÍTICO"),
        "altos":    sum(1 for r in registros if r["veredicto_ia"] == "ALTO"),
        "medios":   sum(1 for r in registros if r["veredicto_ia"] == "MEDIO"),
        "bajos":    sum(1 for r in registros if r["veredicto_ia"] == "BAJO"),
        "buzones":  len(ids_buzones),
    }
    return stats, registros


def construir_reporte_parcial(nombre: str, stats: dict, hora_corte: str) -> str:
    """
    Reporte breve para 06:00 y 14:00.
    Muestra los conteos acumulados hasta la hora actual.
    """
    hoy     = datetime.date.today().strftime("%d/%m/%Y")
    total   = stats.get("total", 0)
    criticos = stats.get("criticos", 0)
    altos   = stats.get("altos", 0)
    medios  = stats.get("medios", 0)
    bajos   = stats.get("bajos", 0)
    buzones = stats.get("buzones", 1)

    if criticos > 0:
        estado = "🔴 Se detectaron amenazas críticas."
    elif altos > 0:
        estado = "🟠 Se detectaron correos sospechosos."
    elif total == 0:
        estado = "🟢 Sin actividad registrada aún."
    else:
        estado = "🟢 Sin amenazas hasta el momento."

    return (
        f"📋 *REPORTE PARCIAL ({hora_corte}) — Vigilante SOC*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Cliente:* {nombre}\n"
        f"📅 *Fecha:* {hoy}\n"
        f"📫 *Buzones vigilados:* {buzones}\n\n"
        f"{estado}\n\n"
        f"📈 *Correos analizados hasta las {hora_corte}:* {total}\n"
        f"   🚨 Crítico:  {criticos}\n"
        f"   ⚠️  Alto:     {altos}\n"
        f"   🟡 Medio:    {medios}\n"
        f"   🟢 Bajo:     {bajos}\n\n"
        f"_Vigilante SOC está activo 24/7 protegiéndote._"
    )


def generar_tip_seguridad_ia(registros: list) -> str:
    """
    Usa Groq para generar un tip de seguridad personalizado basado
    en los tipos de amenazas detectadas durante el día.
    Si no hubo amenazas, genera un tip genérico útil.
    """
    try:
        amenazas = [r for r in registros if r["veredicto_ia"] in ["CRÍTICO", "ALTO"]]

        if amenazas:
            contexto_amenazas = "\n".join(
                f"- De: {r.get('remitente','?')[:60]} | Asunto: {r.get('asunto','?')[:60]} | Nivel: {r['veredicto_ia']}"
                for r in amenazas[:5]
            )
            prompt = (
                f"Hoy se detectaron estas amenazas en la bandeja de un usuario:\n"
                f"{contexto_amenazas}\n\n"
                f"Generá UN solo consejo de seguridad práctico (máximo 2 oraciones) "
                f"basado específicamente en el tipo de ataque detectado, en español, "
                f"tono amigable y directo, orientado a un usuario no técnico. "
                f"No uses markdown, no uses listas. Solo el texto del consejo."
            )
        else:
            # Tip rotativo según el día de la semana para variedad
            tips_genericos = [
                "Verificá siempre que el dominio del remitente coincida con la empresa real antes de hacer clic en cualquier enlace.",
                "Nunca ingreses tu contraseña en un sitio al que llegaste desde un correo. Escribí la URL directamente en el navegador.",
                "Activá la verificación en dos pasos en tu cuenta de Gmail y Outlook. Es la mejor defensa contra el robo de cuentas.",
                "Si recibís un correo urgente de tu banco, llamalos directamente en lugar de hacer clic en los links del correo.",
                "Los correos de phishing suelen tener errores de ortografía o dominios ligeramente diferentes al original.",
                "Nunca abras adjuntos inesperados, aunque vengan de contactos conocidos. Su cuenta puede estar comprometida.",
                "Revisá periódicamente qué aplicaciones tienen acceso a tu cuenta de Google en myaccount.google.com/permissions.",
            ]
            import datetime as dt_module
            tip_del_dia = tips_genericos[dt_module.date.today().weekday() % len(tips_genericos)]
            return tip_del_dia

        client     = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            temperature=0.7,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ⚠️  No se pudo generar el tip de IA: {e}")
        return "Nunca hagas clic en enlaces de correos no solicitados, aunque parezcan legítimos."


def construir_reporte_completo(nombre: str, stats: dict, registros: list) -> str:
    """
    Reporte completo para las 22:00.
    Incluye conteos, listado de hasta 5 correos CRÍTICO y 5 ALTO, y tip de IA.
    """
    hoy      = datetime.date.today().strftime("%d/%m/%Y")
    total    = stats.get("total", 0)
    criticos = stats.get("criticos", 0)
    altos    = stats.get("altos", 0)
    medios   = stats.get("medios", 0)
    bajos    = stats.get("bajos", 0)
    buzones  = stats.get("buzones", 1)

    if criticos > 0:
        estado_dia = "🔴 *Día de alto riesgo* — se detectaron amenazas críticas."
    elif altos > 0:
        estado_dia = "🟠 *Día con alertas* — se detectaron correos sospechosos."
    elif total == 0:
        estado_dia = "🟢 *Sin actividad* — no llegaron correos nuevos hoy."
    else:
        estado_dia = "🟢 *Día tranquilo* — sin amenazas."

    # Secciones de detalle CRÍTICO y ALTO (máx 5 cada uno)
    criticos_list = [r for r in registros if r["veredicto_ia"] == "CRÍTICO"][:5]
    altos_list    = [r for r in registros if r["veredicto_ia"] == "ALTO"][:5]

    detalle = ""
    if criticos_list:
        detalle += "\n🚨 *Correos CRÍTICOS detectados:*\n"
        for i, r in enumerate(criticos_list, 1):
            rem = (r.get("remitente") or "Desconocido")[:55]
            asu = (r.get("asunto") or "Sin asunto")[:55]
            detalle += f"   {i}. De: `{rem}`\n      Asunto: _{asu}_\n"

    if altos_list:
        detalle += "\n⚠️ *Correos de nivel ALTO:*\n"
        for i, r in enumerate(altos_list, 1):
            rem = (r.get("remitente") or "Desconocido")[:55]
            asu = (r.get("asunto") or "Sin asunto")[:55]
            detalle += f"   {i}. De: `{rem}`\n      Asunto: _{asu}_\n"

    # Tip de seguridad generado por IA
    tip = generar_tip_seguridad_ia(registros)

    return (
        f"📊 *REPORTE DIARIO COMPLETO — Vigilante SOC*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Cliente:* {nombre}\n"
        f"📅 *Fecha:* {hoy}\n"
        f"📫 *Buzones vigilados:* {buzones}\n\n"
        f"{estado_dia}\n\n"
        f"📈 *Correos analizados hoy:* {total}\n"
        f"   🚨 Crítico:  {criticos}\n"
        f"   ⚠️  Alto:     {altos}\n"
        f"   🟡 Medio:    {medios}\n"
        f"   🟢 Bajo:     {bajos}\n"
        f"{detalle}\n"
        f"💡 *Consejo de seguridad del día:*\n"
        f"_{tip}_\n\n"
        f"_Vigilante SOC está activo 24/7 protegiéndote._"
    )


def _get_clientes_activos() -> list:
    """Devuelve lista de clientes activos con Telegram configurado."""
    res = supabase.table("clientes").select(
        "id, nombre, telegram_chat_id, telegram_bot_token, estado_suscripcion"
    ).eq("estado_suscripcion", "activo").execute()
    return [c for c in (res.data or []) if c.get("telegram_chat_id")]


def enviar_reporte_parcial(hora_corte: str):
    """Envía el reporte parcial de las 06:00 o 14:00."""
    print(f"\n📋 Enviando reporte parcial ({hora_corte})...")
    hoy_inicio = datetime.datetime.combine(
        datetime.date.today(), datetime.time.min
    ).isoformat()

    try:
        for cliente in _get_clientes_activos():
            stats, _ = _obtener_stats_cliente(cliente["id"], hoy_inicio)
            if not stats:
                continue
            mensaje = construir_reporte_parcial(cliente["nombre"], stats, hora_corte)
            enviar_telegram(cliente["telegram_chat_id"], cliente["telegram_bot_token"], mensaje)
    except Exception as e:
        print(f"   ❌ Error en reporte parcial: {e}")
        traceback.print_exc()


def enviar_reporte_completo():
    """Envía el reporte completo del día a las 22:00 con detalle y tip de IA."""
    print("\n📊 Enviando reporte completo del día (22:00)...")
    hoy_inicio = datetime.datetime.combine(
        datetime.date.today(), datetime.time.min
    ).isoformat()

    try:
        for cliente in _get_clientes_activos():
            stats, registros = _obtener_stats_cliente(cliente["id"], hoy_inicio)
            if not stats:
                continue
            mensaje = construir_reporte_completo(cliente["nombre"], stats, registros)
            enviar_telegram(cliente["telegram_chat_id"], cliente["telegram_bot_token"], mensaje)
    except Exception as e:
        print(f"   ❌ Error en reporte completo: {e}")
        traceback.print_exc()


# ============================================================
# IA
# ============================================================

def analizar_con_groq(contexto: str, links_peligrosos: list, modelo: str) -> str:
    try:
        alerta_links = ""
        if links_peligrosos:
            alerta_links = f"\n⚠️ ALERTA TÉCNICA — Links analizados:\n{resumen_links(links_peligrosos)}"

        client     = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model=modelo,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un Analista de Ciberseguridad especializado en detección de phishing. "
                        "Tu objetivo es detectar amenazas REALES sin generar falsos positivos. "
                        "Sé conservador — solo clasifica como amenaza cuando hay señales claras.\n\n"

                        "== REGLAS DE CLASIFICACIÓN ==\n\n"

                        "CRÍTICO — Solo cuando hay evidencia clara de ataque:\n"
                        "  • Remitente con dominio sospechoso que IMITA una marca (ej: bancogalicia@mopc.cc, microsoft@notif-fake.com)\n"
                        "  • Phishing clásico: pide credenciales, contraseña, datos bancarios o código de verificación\n"
                        "  • URL acortada + urgencia para hacer clic\n"
                        "  • Dominio del remitente NO coincide con la empresa que dice ser\n"
                        "  • Correo en idioma extranjero con urgencia de pago (peaje, multa, fiscal)\n\n"

                        "ALTO — Señales de alerta moderadas:\n"
                        "  • Remitente desconocido con lenguaje urgente o coercitivo\n"
                        "  • Adjunto inesperado de remitente no conocido\n"
                        "  • Link que no coincide con el dominio del remitente\n"
                        "  • Correo en SPAM/JUNK con urgencia de acción\n\n"

                        "MEDIO — Spam o marketing no solicitado:\n"
                        "  • Newsletters, promociones, ofertas comerciales\n"
                        "  • Correos de apuestas, sorteos, regalos\n"
                        "  • Marketing agresivo aunque use lenguaje urgente\n\n"

                        "BAJO — Correos legítimos y normales:\n"
                        "  • Notificaciones de servicios conocidos: Supabase, GitHub, Google, Microsoft, Disney+, Netflix, Binance\n"
                        "  • Facturas y avisos de servicios contratados: AySA, MetroGAS, bancos (Santander, ICBC, Galicia) desde sus dominios oficiales\n"
                        "  • Alertas de seguridad de plataformas reales (nuevo inicio de sesión, almacenamiento lleno)\n"
                        "  • Correos de trabajo, reuniones, documentos compartidos\n"
                        "  • Correos entre personas conocidas\n\n"

                        "== REGLA CLAVE ANTIFALSOPOSITIVOS ==\n"
                        "Si el dominio del remitente es el dominio OFICIAL de la empresa (ej: @supabase.com, @metrogas.com.ar, "
                        "@bancosantander-mail.es, @ses.binance.com, @mail2.disneyplus.com), clasificalo como BAJO aunque "
                        "el asunto parezca urgente. Los servicios legítimos usan lenguaje urgente con frecuencia.\n\n"

                        "== FORMATO DE RESPUESTA — MUY IMPORTANTE ==\n"
                        "Responde ÚNICAMENTE con UNA SOLA PALABRA, sin explicaciones, sin puntos, sin saltos de línea.\n"
                        "La única respuesta válida es exactamente una de estas cuatro palabras: CRÍTICO, ALTO, MEDIO, BAJO.\n"
                        "Cualquier otra respuesta es incorrecta."
                    )
                },
                {"role": "user", "content": contexto + alerta_links}
            ]
        )
        respuesta_raw = completion.choices[0].message.content.strip().upper()

        # Sanitizar — extraer solo la primera palabra válida de la respuesta
        # Esto previene que el modelo devuelva explicaciones adicionales
        PALABRAS_VALIDAS = ["CRÍTICO", "ALTO", "MEDIO", "BAJO"]
        for palabra in PALABRAS_VALIDAS:
            if respuesta_raw.startswith(palabra):
                return palabra

        # Si no empieza con una palabra válida, buscarla en el texto
        for palabra in PALABRAS_VALIDAS:
            if palabra in respuesta_raw:
                print(f"   ⚠️  Modelo devolvió respuesta extensa — extraído: {palabra}")
                return palabra

        # Si no encontró ninguna palabra válida, retornar ERROR_IA
        print(f"   ⚠️  Respuesta inesperada del modelo: {respuesta_raw[:50]}")
        return "ERROR_IA"

    except Exception as e:
        print(f"   ❌ Error IA {modelo}: {e}")
        return "ERROR_IA"


def procesar_correo_ia(contexto: str, links_peligrosos: list) -> str:
    print("   🧠 Triaje rápido (GPT-OSS-20B)...")
    triaje = analizar_con_groq(contexto, links_peligrosos, "openai/gpt-oss-20b")
    if triaje in ["MEDIO", "ALTO", "CRÍTICO"]:
        print(f"   ⚠️  Triaje: '{triaje}' → Escalando a GPT-OSS-120B...")
        ctx_esc = f"Análisis previo sospechoso ({triaje}). Confirma el veredicto:\n\n{contexto}"
        return analizar_con_groq(ctx_esc, links_peligrosos, "openai/gpt-oss-120b")
    print("   ✅ Correo limpio.")
    return triaje


def explicar_amenaza_ia(contexto: str, veredicto: str, links_peligrosos: list) -> str:
    """
    Genera una explicación en lenguaje humano de por qué el correo es peligroso.
    Solo se llama para veredictos CRÍTICO y ALTO.
    Inspirado en el enfoque de analista SOC junior del SOC-Lab.
    """
    try:
        links_str = resumen_links(links_peligrosos) if links_peligrosos else "Ninguno"
        prompt = (
            f"Sos un analista de ciberseguridad que explica amenazas a usuarios sin conocimientos técnicos.\n\n"
            f"Se detectó un correo clasificado como {veredicto}. Estos son los datos:\n\n"
            f"{contexto}\n"
            f"Links sospechosos: {links_str}\n\n"
            f"En exactamente 2 oraciones cortas:\n"
            f"1. Explicá en lenguaje simple qué tipo de ataque es y por qué es peligroso\n"
            f"2. Decí qué debe hacer el usuario: NO abrir, NO hacer clic, eliminar, etc\n\n"
            f"Tono: directo, sin tecnicismos, sin markdown, sin listas. Solo texto plano."
        )

        client = Groq(api_key=GROQ_API_KEY)
        resp   = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            temperature=0.3,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ⚠️  No se pudo generar explicación IA: {e}")
        return ""


# ============================================================
# PUNTO 8: GUARDAR TODOS LOS CORREOS (no solo amenazas)
# ============================================================

def guardar_analisis(id_buzon, msg_id, remitente, asunto, resultado, links, ubicacion):
    """
    Guarda el resultado del análisis en Supabase.
    Punto 8: guarda TODOS los correos (CRÍTICO, ALTO, MEDIO, BAJO, ERROR_IA).
    Así el dashboard puede mostrar el volumen real de correos analizados.
    """
    try:
        supabase.table("amenazas_detectadas").upsert({
            "buzon_id":           id_buzon,
            "id_mensaje_correo":  str(msg_id),
            "remitente":          remitente,
            "asunto":             asunto,
            "veredicto_ia":       resultado,
            "links_sospechosos":  resumen_links(links) if links else "Ninguno",
            "carpeta":            ubicacion
        }, on_conflict="buzon_id,id_mensaje_correo").execute()

        es_amenaza = resultado in ["CRÍTICO", "ALTO"]
        icono = "💾" if not es_amenaza else "✅"
        print(f"   {icono} Guardado en BD ({resultado}).")
        return True
    except Exception as e:
        print(f"   ❌ Error guardando en BD: {e}")
        return False


# ============================================================
# PUNTO 3: DETECTOR DE TOKEN VENCIDO / REVOCADO
# ============================================================

def marcar_buzon_token_expirado(id_buzon: str, email_cuenta: str, nombre_cliente: str,
                                 chat_id, bot_token):
    try:
        supabase.table("buzones_monitoreados").update({
            "estado_proteccion": "token_expirado"
        }).eq("id", id_buzon).execute()
        print(f"   🔒 Buzón {email_cuenta} marcado como token_expirado en BD.")
    except Exception as e:
        print(f"   ❌ Error actualizando estado del buzón: {e}")

    # Detectar proveedor para armar el link correcto
    try:
        res = supabase.table("buzones_monitoreados").select(
            "proveedor, cliente_id"
        ).eq("id", id_buzon).execute()
        proveedor = res.data[0]["proveedor"] if res.data else "google"
        cliente_id = res.data[0]["cliente_id"] if res.data else ""
    except:
        proveedor = "google"
        cliente_id = ""

    # Armar link de reconexión según proveedor
    if proveedor == "google":
        link = f"https://api.vigilantesoc.com.ar/api/auth/google/callback"
        boton = "🌐 Reconectar Gmail"
        # Link completo con OAuth
        from urllib.parse import quote
        scope = quote("https://www.googleapis.com/auth/userinfo.email https://mail.google.com/")
        link = (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={os.getenv('GOOGLE_CLIENT_ID')}&"
            f"redirect_uri=https://api.vigilantesoc.com.ar/api/auth/google/callback&"
            f"response_type=code&scope={scope}&"
            f"access_type=offline&prompt=consent&state={cliente_id}"
        )
    else:
        link = f"https://api.vigilantesoc.com.ar/api/auth/microsoft/login?cliente_id={cliente_id}"
        boton = "🟦 Reconectar Outlook"

    # Avisar al cliente con link directo
    if chat_id:
        mensaje = (
            f"🔒 *Acción requerida — Vigilante SOC*\n\n"
            f"La conexión con tu buzón `{email_cuenta}` ha expirado.\n\n"
            f"Tu cuenta *dejó de estar protegida*.\n\n"
            f"👉 *Tocá el link para reconectar en un clic:*\n"
            f"[{boton}]({link})\n\n"
            f"_O entrá a tu dashboard y usá el botón 'Añadir Gmail' o 'Añadir Outlook'._"
        )
        enviar_telegram(chat_id, bot_token, mensaje)

    # Avisar al admin
    aviso_admin = (
        f"⚠️ *TOKEN EXPIRADO*\n\n"
        f"Cliente: {nombre_cliente}\n"
        f"Buzón: `{email_cuenta}`\n"
        f"Proveedor: {proveedor}\n"
        f"Estado: `token_expirado`"
    )
    enviar_telegram(TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_TOKEN_DEFAULT, aviso_admin)


def marcar_buzon_token_expirado_Viejo(id_buzon: str, email_cuenta: str, nombre_cliente: str,
                                 chat_id, bot_token):
    """
    Marca el buzón como 'token_expirado' en la BD y avisa al cliente
    para que reconecte su cuenta.
    """
    try:
        supabase.table("buzones_monitoreados").update({
            "estado_proteccion": "token_expirado"
        }).eq("id", id_buzon).execute()
        print(f"   🔒 Buzón {email_cuenta} marcado como token_expirado en BD.")
    except Exception as e:
        print(f"   ❌ Error actualizando estado del buzón: {e}")

    # Avisar al cliente por Telegram
    if chat_id:
        mensaje = (
            f"🔒 *Acción requerida — Vigilante SOC*\n\n"
            f"La conexión con tu buzón `{email_cuenta}` ha expirado o fue revocada.\n\n"
            f"Tu cuenta *dejó de estar protegida*. Por favor volvé a vincularla "
            f"desde el panel de control para reactivar la vigilancia.\n\n"
            f"👉 Accedé a tu dashboard y usá el botón *'Añadir Gmail'* o *'Añadir Outlook'*."
        )
        enviar_telegram(chat_id, bot_token, mensaje)

    # Avisar también al admin
    aviso_admin = (
        f"⚠️ *TOKEN EXPIRADO*\n\n"
        f"Cliente: {nombre_cliente}\n"
        f"Buzón: `{email_cuenta}`\n"
        f"Estado actualizado a: `token_expirado`"
    )
    enviar_telegram(TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_TOKEN_DEFAULT, aviso_admin)


def avisar_token_por_vencer(id_buzon: str, email_cuenta: str, nombre_cliente: str,
                             chat_id, bot_token, dias_restantes: int):
    """
    Avisa al cliente que su token vence pronto para que reconecte
    ANTES de que el sistema deje de protegerlo.
    Solo se llama cuando la renovación automática no es posible.
    """
    if not chat_id:
        return
    mensaje = (
        f"⏰ *Aviso preventivo — Vigilante SOC*\n\n"
        f"La conexión con tu buzón `{email_cuenta}` vencerá "
        f"en aproximadamente *{dias_restantes} día(s)*.\n\n"
        f"Para evitar interrupciones en tu protección, te recomendamos "
        f"reconectar tu cuenta antes de que expire.\n\n"
        f"👉 Entrá a tu dashboard → *'Añadir Gmail'* o *'Añadir Outlook'*."
    )
    enviar_telegram(chat_id, bot_token, mensaje)
    print(f"   ⏰ Aviso preventivo enviado a {nombre_cliente} ({email_cuenta}).")


def es_error_de_autenticacion(e: Exception) -> bool:
    """
    Detecta si un error es por token vencido/revocado o permisos insuficientes
    en Google o Microsoft.
    """
    mensaje = str(e).lower()
    # Google: RefreshError, invalid_grant, Token has been expired or revoked
    if any(p in mensaje for p in [
        "invalid_grant", "token has been expired", "token has been revoked",
        "refresherror", "invalid credentials", "401"
    ]):
        return True
    # Google: 403 insufficientPermissions — token sin scope de Gmail
    # Pasa cuando el usuario reconecta sin otorgar permiso de lectura de correos
    if "403" in mensaje and any(p in mensaje for p in [
        "insufficientpermissions", "insufficient permission",
        "insufficient authentication scopes", "request had insufficient"
    ]):
        return True
    # Microsoft: interaction_required, invalid_grant
    if any(p in mensaje for p in [
        "interaction_required", "invalid_grant", "unauthorized",
        "refresh token", "aadsts"
    ]):
        return True
    return False


# ============================================================
# RENOVACIÓN PROACTIVA DE TOKENS
# ============================================================

def renovar_y_guardar_token_google(buzon, credenciales) -> bool:
    """
    Si Google devolvió un nuevo access_token durante el refresh,
    verifica si también renovó el refresh_token y lo guarda en Supabase.
    Google rota el refresh_token silenciosamente en algunas situaciones.
    Retorna True si guardó uno nuevo, False si no hubo cambios.
    """
    try:
        # Después de refrescar, google-auth actualiza credenciales.token
        # pero el refresh_token puede cambiar si Google lo rotó
        nuevo_refresh = credenciales.refresh_token
        if not nuevo_refresh:
            return False

        # Comparamos con el que tenemos guardado
        actual_cifrado  = buzon.get('refresh_token_cifrado', '')
        actual_descif   = descifrar2(actual_cifrado) if actual_cifrado else ''

        if nuevo_refresh != actual_descif:
            # Google nos dio un refresh_token nuevo — lo guardamos cifrado
            nuevo_cifrado = cipher_suite.encrypt(nuevo_refresh.encode()).decode()
            supabase.table("buzones_monitoreados").update({
                "refresh_token_cifrado": nuevo_cifrado
            }).eq("id", buzon['id']).execute()
            print(f"   🔄 Refresh token renovado y guardado en BD para {buzon['email_cuenta']}.")
            return True
        return False
    except Exception as e:
        print(f"   ⚠️  No se pudo guardar el token renovado: {e}")
        return False


def token_google_cerca_de_vencer(credenciales) -> bool:
    """
    Devuelve True si el access_token vence en menos de 10 minutos.
    Esto fuerza un refresh proactivo antes de que expire.
    """
    try:
        if credenciales.expiry is None:
            return True  # Sin fecha de expiración → mejor refrescar
        ahora    = datetime.datetime.now(datetime.UTC)
        margen   = datetime.timedelta(minutes=10)
        return credenciales.expiry - ahora < margen
    except Exception:
        return True


# ============================================================
# CAMINO 1: GMAIL
# ============================================================

def procesar_gmail(buzon, datos_cliente):
    """
    Lee correos NO LEÍDOS de las últimas 24hs (newer_than:1d).
    Máximo MAX_CORREOS_POR_CARPETA por carpeta (INBOX + SPAM).
    Punto 3: detecta tokens vencidos y avisa al cliente.
    Punto 8: guarda TODOS los correos analizados.
    """
    email_cuenta   = buzon['email_cuenta']
    id_buzon       = buzon['id']
    chat_id        = datos_cliente.get('telegram_chat_id')
    bot_token      = datos_cliente.get('telegram_bot_token')
    nombre_cliente = datos_cliente.get('nombre', email_cuenta)

    try:
        refresh_token = descifrar2(buzon['refresh_token_cifrado'])
        credenciales  = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET")
        )

        # Renovación proactiva: refrescamos siempre que el token esté
        # por vencer o no tenga access_token cargado en memoria.
        # Si Google rota el refresh_token, lo capturamos y guardamos.
        try:
            if token_google_cerca_de_vencer(credenciales) or not credenciales.token:
                print(f"   🔄 Renovando token Google para {email_cuenta}...")
                credenciales.refresh(Request())
                renovar_y_guardar_token_google(buzon, credenciales)
        except Exception as refresh_error:
            if es_error_de_autenticacion(refresh_error):
                print(f"   🔒 Token de Google vencido/revocado para {email_cuenta}.")
                marcar_buzon_token_expirado(id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token)
                return
            raise

        service = build('gmail', 'v1', credentials=credenciales)
        total   = 0

        for label_id, ubicacion in [("INBOX", "INBOX"), ("SPAM", "SPAM")]:
            print(f"   📂 Gmail [{ubicacion}] — máx. {MAX_CORREOS_POR_CARPETA} del día...")

            try:
                res = service.users().messages().list(
                    userId='me',
                    labelIds=[label_id],
                    q="is:unread newer_than:1d",
                    maxResults=MAX_CORREOS_POR_CARPETA
                ).execute()
            except Exception as list_error:
                # 403 insufficientPermissions — token sin scope de Gmail
                # Pasa cuando el usuario reconectó sin otorgar permiso de lectura
                if es_error_de_autenticacion(list_error):
                    print(f"   🔒 Permisos insuficientes en Gmail para {email_cuenta}.")
                    print(f"   💡 El usuario debe reconectar otorgando permiso de lectura.")
                    marcar_buzon_token_expirado(
                        id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token
                    )
                    return  # Salir completamente — no seguir con SPAM
                raise   # Otro error → sube al handler general

            mensajes = res.get('messages', [])

            if not mensajes:
                print(f"   ✅ {ubicacion} sin novedades.")
                continue

            print(f"   📬 {len(mensajes)} correo(s) no leído(s) en {ubicacion}.")

            for m in mensajes:
                msg_id = m['id']
                if msg_id in HISTORIAL_IDS:
                    continue
                HISTORIAL_IDS.add(msg_id)
                guardar_id_en_disco(msg_id)   # Punto 7

                # Marcar como leído (sin Mail.ReadWrite puede fallar silenciosamente, no es crítico)
                try:
                    service.users().messages().batchModify(
                        userId='me',
                        body={'ids': [msg_id], 'removeLabelIds': ['UNREAD']}
                    ).execute()
                except Exception:
                    pass

                msg_completo   = service.users().messages().get(userId='me', id=msg_id).execute()
                etiquetas      = msg_completo.get('labelIds', [])
                ubicacion_real = "SPAM" if "SPAM" in etiquetas else "INBOX"
                headers        = msg_completo['payload']['headers']
                remitente      = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Desconocido")
                asunto         = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "Sin Asunto")
                snippet        = msg_completo.get('snippet', '')

                print(f"   📧 De: {remitente[:60]}")
                print(f"      Asunto: {asunto[:60]}")

                links     = detectar_links_sospechosos(snippet)
                contexto  = f"REMITENTE: {remitente}\nASUNTO: {asunto}\nCONTENIDO: {snippet}"
                resultado = procesar_correo_ia(contexto, links)
                icon      = get_status_icon(resultado)

                print(f"   {icon} Veredicto: {resultado}")
                total += 1

                # Punto 8: guardar TODOS
                guardar_analisis(id_buzon, msg_id, remitente, asunto, resultado, links, ubicacion_real)

                # Alerta solo si es peligro
                if resultado in ["CRÍTICO", "ALTO"] and chat_id:
                    print(f"   🤖 Generando explicación IA para el cliente...")
                    explicacion = explicar_amenaza_ia(contexto, resultado, links)
                    informe = construir_alerta(icon, ubicacion_real, email_cuenta,
                                               remitente, asunto, resultado, links,
                                               explicacion_ia=explicacion)
                    enviar_telegram(chat_id, bot_token, informe)

        print(f"   📊 Gmail: {total} correo(s) procesado(s).")

    except Exception as e:
        # Punto 3: capturar errores de auth que no se detectaron antes
        if es_error_de_autenticacion(e):
            print(f"   🔒 Error de autenticación Gmail {email_cuenta}: {e}")
            marcar_buzon_token_expirado(id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token)
        else:
            print(f"   ❌ Error crítico Gmail {email_cuenta}:")
            traceback.print_exc()


# ============================================================
# CAMINO 2: MICROSOFT (HOTMAIL / OUTLOOK) — Graph API
# ============================================================

def procesar_microsoft(buzon, datos_cliente):
    """
    Lee correos NO LEÍDOS recibidos HOY.
    Máximo MAX_CORREOS_POR_CARPETA por carpeta (INBOX + JUNK).
    Punto 3: detecta tokens vencidos y avisa al cliente.
    Punto 8: guarda TODOS los correos analizados.
    Solo usa Mail.Read (sin Mail.ReadWrite) — punto 4 resuelto.
    """
    email_cuenta   = buzon['email_cuenta']
    id_buzon       = buzon['id']
    chat_id        = datos_cliente.get('telegram_chat_id')
    bot_token      = datos_cliente.get('telegram_bot_token')
    nombre_cliente = datos_cliente.get('nombre', email_cuenta)

    try:
        refresh_token = descifrar2(buzon['refresh_token_cifrado'])

        # Punto 3: si Microsoft devuelve error de auth, es token muerto
        resultado_ms = msal_app.acquire_token_by_refresh_token(
            refresh_token,
            scopes=["https://graph.microsoft.com/Mail.Read", "User.Read"]
        )

        if "error" in resultado_ms:
            error_code = resultado_ms.get("error", "")
            error_desc = resultado_ms.get("error_description", "")
            print(f"   ❌ Error token Microsoft: {error_code} — {error_desc}")

            # Punto 3: detectar si el token está muerto
            codigos_auth = ["invalid_grant", "interaction_required", "unauthorized_client"]
            if any(c in error_code.lower() for c in codigos_auth):
                marcar_buzon_token_expirado(id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token)
            return

        access_token = resultado_ms["access_token"]
        ms_headers   = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type':  'application/json'
        }

        hoy_inicio = datetime.datetime.combine(
            datetime.date.today(), datetime.time.min
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        total = 0

        for tipo_carpeta, ubicacion in [('inbox', 'INBOX'), ('junkemail', 'JUNK')]:
            print(f"   📂 Microsoft [{ubicacion}] — máx. {MAX_CORREOS_POR_CARPETA} del día...")

            endpoint = (
                f"https://graph.microsoft.com/v1.0/me/mailFolders('{tipo_carpeta}')/messages"
                f"?$filter=isRead eq false and receivedDateTime ge {hoy_inicio}"
                f"&$top={MAX_CORREOS_POR_CARPETA}"
                f"&$orderby=receivedDateTime desc"
                f"&$select=id,subject,sender,body,receivedDateTime"
            )

            try:
                respuesta = httpx.get(endpoint, headers=ms_headers, timeout=30.0)
                datos     = respuesta.json()

                # Punto 3: 401 de Graph API = token revocado
                if respuesta.status_code == 401:
                    print(f"   🔒 Token Microsoft revocado para {email_cuenta}.")
                    marcar_buzon_token_expirado(id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token)
                    return

            except Exception as e:
                print(f"   ❌ Error consultando Graph API ({ubicacion}): {e}")
                continue

            mensajes = datos.get('value', [])
            if not mensajes:
                print(f"   ✅ {ubicacion} sin novedades.")
                continue

            print(f"   📬 {len(mensajes)} correo(s) no leído(s) de hoy en {ubicacion}.")

            for msg in mensajes:
                msg_id = msg.get('id')
                if not msg_id or msg_id in HISTORIAL_IDS:
                    continue
                HISTORIAL_IDS.add(msg_id)
                guardar_id_en_disco(msg_id)   # Punto 7

                asunto      = msg.get('subject', 'Sin Asunto') or 'Sin Asunto'
                remitente   = msg.get('sender', {}).get('emailAddress', {}).get('address', 'Desconocido')
                cuerpo_html = msg.get('body', {}).get('content', '') or ''

                texto_limpio = re.sub(r'<[^>]+>', ' ', cuerpo_html)
                texto_limpio = re.sub(r'\s+', ' ', texto_limpio).strip()[:1500]

                print(f"   📧 De: {remitente[:60]}")
                print(f"      Asunto: {asunto[:60]}")

                # Marcar leído sin ReadWrite — solo intentamos, no falla si no puede
                try:
                    httpx.patch(
                        f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}",
                        headers=ms_headers, json={"isRead": True}, timeout=10.0
                    )
                except Exception:
                    pass

                links     = detectar_links_sospechosos(cuerpo_html)
                contexto  = f"REMITENTE: {remitente}\nASUNTO: {asunto}\nCONTENIDO: {texto_limpio}"
                resultado = procesar_correo_ia(contexto, links)
                icon      = get_status_icon(resultado)

                print(f"   {icon} Veredicto: {resultado}")
                total += 1

                # Punto 8: guardar TODOS
                guardar_analisis(id_buzon, msg_id, remitente, asunto, resultado, links, ubicacion)

                if resultado in ["CRÍTICO", "ALTO"] and chat_id:
                    print(f"   🤖 Generando explicación IA para el cliente...")
                    explicacion = explicar_amenaza_ia(contexto, resultado, links)
                    informe = construir_alerta(icon, ubicacion, email_cuenta,
                                               remitente, asunto, resultado, links,
                                               explicacion_ia=explicacion)
                    enviar_telegram(chat_id, bot_token, informe)

        print(f"   📊 Microsoft: {total} correo(s) procesado(s).")

    except Exception as e:
        if es_error_de_autenticacion(e):
            print(f"   🔒 Error de autenticación Microsoft {email_cuenta}: {e}")
            marcar_buzon_token_expirado(id_buzon, email_cuenta, nombre_cliente, chat_id, bot_token)
        else:
            print(f"   ❌ Error crítico Microsoft {email_cuenta}:")
            traceback.print_exc()


# ============================================================
# DESPACHADOR
# ============================================================

def procesar_buzon(buzon, datos_cliente):
    email_cuenta = buzon['email_cuenta']
    proveedor    = buzon['proveedor']
    nombre       = datos_cliente.get('nombre', email_cuenta)

    print(f"\n{'─'*55}")
    print(f"🕵️  {email_cuenta} | {nombre} | {proveedor.upper()}")
    print(f"{'─'*55}")

    chat_id   = datos_cliente.get('telegram_chat_id')
    bot_token = datos_cliente.get('telegram_bot_token')

    if not chat_id:
        print(f"   ⚠️  Sin Chat ID → alertas desactivadas para este usuario.")
    elif not es_chat_id_valido(str(chat_id)):
        print(f"   ⚠️  Chat ID '{chat_id}' inválido (debe ser número).")
    if bot_token and not es_token_valido(bot_token):
        print(f"   ⚠️  Token de bot inválido → se usará el bot global.")

    if proveedor == "google":
        procesar_gmail(buzon, datos_cliente)
    elif proveedor == "microsoft":
        procesar_microsoft(buzon, datos_cliente)
    else:
        print(f"   ❌ Proveedor desconocido: '{proveedor}'.")



# ============================================================
# BUCLE MAESTRO
# ============================================================

def chequear_tokens_proximos_a_vencer():
    """
    Revisa todos los buzones activos y avisa a los clientes cuyo
    refresh_token lleva más de 5 días sin renovarse (en modo Testing
    de Google Cloud los tokens duran 7 días).
    Se ejecuta una vez por día desde el bucle maestro.
    """
    print("\n🔍 Chequeando tokens próximos a vencer...")
    try:
        # Buscamos buzones activos de Google
        res = supabase.table("buzones_monitoreados").select(
            "id, email_cuenta, cliente_id, updated_at, "
            "clientes!inner(nombre, telegram_chat_id, telegram_bot_token)"
        ).eq("estado_proteccion", "activo") \
         .eq("proveedor", "google").execute()

        buzones = res.data or []
        ahora   = datetime.datetime.now(datetime.UTC)

        for b in buzones:
            updated_str = b.get("updated_at") or b.get("created_at")
            if not updated_str:
                continue
            try:
                # Parseamos la fecha de última actualización del registro
                ultima_actualizacion = datetime.datetime.fromisoformat(
                    updated_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
                dias_sin_renovar = (ahora - ultima_actualizacion).days

                # Si lleva 5+ días sin renovarse, avisamos (margen de 2 días antes de los 7)
                if dias_sin_renovar >= 5:
                    datos_cliente = b.get("clientes", {})
                    avisar_token_por_vencer(
                        b["id"],
                        b["email_cuenta"],
                        datos_cliente.get("nombre", b["email_cuenta"]),
                        datos_cliente.get("telegram_chat_id"),
                        datos_cliente.get("telegram_bot_token"),
                        dias_restantes=7 - dias_sin_renovar
                    )
            except Exception as e:
                print(f"   ⚠️  Error procesando fecha de {b.get('email_cuenta')}: {e}")

    except Exception as e:
        print(f"   ❌ Error chequeando tokens: {e}")
        traceback.print_exc()


# ============================================================
# BUCLE MAESTRO
# ============================================================

def enviar_heartbeat(buzones_activos: int, ciclos_ok: int, ciclos_error: int, detalle_clientes: list):
    """
    Envía dos tipos de mensajes:
    1. Al ADMIN: resumen total con desglose por cliente
    2. A cada CLIENTE: solo sus buzones activos
    """
    ahora  = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    uptime = f"{ciclos_ok} ciclos OK / {ciclos_error} con error"

    if ciclos_error == 0:
        estado = "✅ Todo funcionando correctamente."
    elif ciclos_error <= 2:
        estado = f"⚠️ {ciclos_error} ciclo(s) con error de red (recuperado solo)."
    else:
        estado = f"🔴 {ciclos_error} errores en la última hora — revisar logs."

    # ── Mensaje al ADMIN con desglose por cliente ─────────
    desglose = ""
    for c in detalle_clientes:
        desglose += f"   • {c['nombre']}: {c['buzones']} buzón(es)\n"

    mensaje_admin = (
        f"💓 *HEARTBEAT — Vigilante SOC*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 *Hora:* {ahora}\n"
        f"📫 *Buzones totales:* {buzones_activos}\n"
        f"{desglose}"
        f"🔄 *Ciclos última hora:* {uptime}\n\n"
        f"{estado}"
    )
    
      
    enviar_telegram(mi_chat_id, mi_bot_token, mensaje_admin)
    print(f"   💓 Heartbeat enviado al admin ({buzones_activos} buzones totales).")

    # ── Mensaje a cada CLIENTE con solo sus buzones ───────
    for c in detalle_clientes:
        chat_id   = c.get("telegram_chat_id")
        bot_token = c.get("telegram_bot_token")
        if not chat_id:
            continue

        # Listar los buzones del cliente con su estado
        lista_buzones = ""
        for b in c.get("lista_buzones", []):
            if b["estado"] == "activo":
                icono = "✅"
            elif b["estado"] == "token_expirado":
                icono = "🔒"
            else:
                icono = "❌"
            lista_buzones += f"   {icono} {b['email']} ({b['proveedor'].upper()})\n"

        mensaje_cliente = (
            f"💓 *Vigilante SOC — Estado de tu cuenta*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 *Hora:* {ahora}\n"
            f"📫 *Tus buzones vigilados:* {c['buzones']}\n"
            f"{lista_buzones}\n"
            f"{estado}"
        )
        enviar_telegram(chat_id, bot_token, mensaje_cliente)
        print(f"   💓 Heartbeat enviado a {c['nombre']}.")


def ejecutar_limpieza_datos():
    """
    Ejecuta la política de retención de datos en Supabase.
    BAJO/MEDIO > 30 días, ALTO/CRÍTICO > 90 días, ERROR_IA > 7 días.
    Se llama una vez por día a las 03:00.
    """
    print("\n🧹 Ejecutando política de retención de datos...")
    try:
        result = supabase.rpc("limpiar_registros_antiguos").execute()
        if result.data:
            r = result.data[0]
            print(f"   ✅ Eliminados: {r.get('eliminados_bajo_medio', 0)} BAJO/MEDIO | "
                  f"{r.get('eliminados_error', 0)} ERROR_IA | "
                  f"{r.get('eliminados_alto_critico', 0)} ALTO/CRÍTICO")
        else:
            print("   ✅ Limpieza completada — sin registros a eliminar.")
    except Exception as e:
        print(f"   ⚠️  Error en limpieza de datos: {e}")


def bucle_principal():
    global HISTORIAL_IDS

    print("🚀 Iniciando Motor de Patrullaje — Vigilante SOC")
    print(f"   Límite por carpeta:  {MAX_CORREOS_POR_CARPETA} correos del día")
    print(f"   Bot global:          {'✅' if TELEGRAM_TOKEN_DEFAULT else '❌ FALTA'}")

    # Punto 7: cargar historial persistente al arrancar
    HISTORIAL_IDS = cargar_historial_desde_disco()

    # Control para tareas diarias
    ultimo_parcial_06     = None
    ultimo_parcial_14     = None
    ultimo_completo_22    = None
    ultimo_chequeo_tokens = None
    ultimo_heartbeat      = None
    ultimo_limpieza       = None   # Retención de datos — 03:00

    # Contadores para el heartbeat
    ciclos_ok    = 0
    ciclos_error = 0
    ultimo_conteo_buzones = 0

    while True:
        ahora     = datetime.datetime.now()
        ahora_iso = ahora.isoformat()

        # ── Reporte parcial 06:00 ─────────────────────────────
        if ahora.hour == 6 and (ultimo_parcial_06 is None or ultimo_parcial_06 != ahora.date()):
            enviar_reporte_parcial("06:00")
            ultimo_parcial_06 = ahora.date()

        # ── Reporte parcial 14:00 ─────────────────────────────
        if ahora.hour == 14 and (ultimo_parcial_14 is None or ultimo_parcial_14 != ahora.date()):
            enviar_reporte_parcial("14:00")
            ultimo_parcial_14 = ahora.date()

        # ── Reporte completo 22:00 ────────────────────────────
        if ahora.hour == 22 and (ultimo_completo_22 is None or ultimo_completo_22 != ahora.date()):
            enviar_reporte_completo()
            ultimo_completo_22 = ahora.date()

        # ── Chequeo de tokens próximos a vencer — 10:00 ──────
        if ahora.hour == 10 and (ultimo_chequeo_tokens is None or ultimo_chequeo_tokens != ahora.date()):
            chequear_tokens_proximos_a_vencer()
            ultimo_chequeo_tokens = ahora.date()

        # ── Limpieza de datos antiguos — 03:00 ───────────────
        if ahora.hour == 3 and (ultimo_limpieza is None or ultimo_limpieza != ahora.date()):
            ejecutar_limpieza_datos()
            ultimo_limpieza = ahora.date()

        # ── Heartbeat cada hora al admin ──────────────────────
        # CADA 3 HORAS:
        ahora_hora = ahora.replace(minute=0, second=0, microsecond=0)
        if ultimo_heartbeat is None or (ahora_hora - ultimo_heartbeat).total_seconds() >= 10800:
            # Construir detalle por cliente consultando Supabase
            try:
                res_clientes = supabase.table("clientes").select(
                    "id, nombre, telegram_chat_id, telegram_bot_token, estado_suscripcion"
                ).eq("estado_suscripcion", "activo").execute()

                detalle_clientes = []
                for cli in (res_clientes.data or []):
                    res_buz = supabase.table("buzones_monitoreados").select(
                        "email_cuenta, proveedor, estado_proteccion"
                    ).eq("cliente_id", cli["id"]).execute()

                    buzones_cli = res_buz.data or []
                    activos = sum(1 for b in buzones_cli if b["estado_proteccion"] == "activo")

                    detalle_clientes.append({
                        "nombre":           cli["nombre"],
                        "telegram_chat_id": cli.get("telegram_chat_id"),
                        "telegram_bot_token": cli.get("telegram_bot_token"),
                        "buzones":          activos,
                        "lista_buzones": [
                            {
                                "email":    b["email_cuenta"],
                                "proveedor": b["proveedor"],
                                "estado":   b["estado_proteccion"]
                            }
                            for b in buzones_cli
                        ]
                    })

                enviar_heartbeat(ultimo_conteo_buzones, ciclos_ok, ciclos_error, detalle_clientes)
            except Exception as e:
                print(f"   ⚠️  Error construyendo detalle del heartbeat: {e}")
                enviar_heartbeat(ultimo_conteo_buzones, ciclos_ok, ciclos_error, [])

            ultimo_heartbeat = ahora_hora
            ciclos_ok    = 0
            ciclos_error = 0
        # ─────────────────────────────────────────────────────

        try:
            res = supabase.table("buzones_monitoreados").select(
                "*, clientes!inner(nombre, estado_suscripcion, fecha_vencimiento, "
                "telegram_chat_id, telegram_bot_token)"
            ).eq("estado_proteccion", "activo") \
             .eq("clientes.estado_suscripcion", "activo") \
             .gt("clientes.fecha_vencimiento", ahora_iso) \
             .execute()

            buzones = res.data or []
            ultimo_conteo_buzones = len(buzones)

            print(f"\n{'='*55}")
            print(f"🕒 {ahora.strftime('%d/%m/%Y %H:%M:%S')} | Buzones activos: {len(buzones)}")
            print(f"{'='*55}")

            for buzon in buzones:
                procesar_buzon(buzon, buzon['clientes'])

            # Limpiar historial en memoria cada 2000 entradas
            if len(HISTORIAL_IDS) > 2000:
                print("🧹 Limpiando historial en memoria (disco intacto)...")
                HISTORIAL_IDS.clear()
                HISTORIAL_IDS = cargar_historial_desde_disco()

            ciclos_ok += 1
            print(f"\n💤 Próxima ronda en 10 minutos...")
            time.sleep(600)

        except Exception as e:
            ciclos_error += 1
            mensaje_error = str(e).lower()

            # Detectar si es error de red / DNS / SSL (Oracle Free Tier)
            es_error_red = any(p in mensaje_error for p in [
                "name resolution", "connecterror", "timeout",
                "eof occurred", "connection refused", "network",
                "ssl", "errno -3", "temporary failure"
            ])

            if es_error_red:
                print(f"\n⚠️  Error de red (probable corte temporal en Oracle):")
                print(f"   {e}")
                print(f"   🔄 Reintentando en 2 minutos...")
                time.sleep(120)
            else:
                print(f"\n❌ Error inesperado en el bucle maestro:")
                traceback.print_exc()
                print(f"   💤 Esperando 5 minutos antes de reintentar...")
                time.sleep(300)


if __name__ == "__main__":
    bucle_principal()

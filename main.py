import os
import msal
import requests
import bcrypt
from datetime import datetime
from fastapi.responses import RedirectResponse
import httpx
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from collections import defaultdict
from time import time


# Cargar variables de entorno
load_dotenv()

# --- CONFIGURACIÓN ---
URL_SUPABASE = os.environ.get("SUPABASE_URL")
KEY_SUPABASE = os.environ.get("SUPABASE_KEY")
LLAVE_MAESTRA = os.environ.get("VIGILANTE_MASTER_KEY")

if not all([URL_SUPABASE, KEY_SUPABASE, LLAVE_MAESTRA]):
    raise ValueError("¡Faltan variables en el archivo .env!")

# Configuración de MSAL (Microsoft)
msal_app = msal.ConfidentialClientApplication(
    os.getenv("MICROSOFT_CLIENT_ID"),
    authority="https://login.microsoftonline.com/consumers",
    client_credential=os.getenv("MICROSOFT_CLIENT_SECRET")
)

# Inicializar clientes
supabase: Client = create_client(URL_SUPABASE, KEY_SUPABASE)
cipher_suite = Fernet(LLAVE_MAESTRA.encode())

# Inicializar aplicación web
app = FastAPI(title="Vigilante Core API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://m.vigilantesoc.com.ar"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting por cliente_id
_rate_limit_store = defaultdict(list)
RATE_LIMIT_MAX    = 100
RATE_LIMIT_WINDOW = 60

def check_rate_limit(cliente_id: str):
    ahora = time()
    ventana = _rate_limit_store[cliente_id]
    # Limpiar requests viejos
    _rate_limit_store[cliente_id] = [t for t in ventana if ahora - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[cliente_id]) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes. Intentá en un momento.")
    _rate_limit_store[cliente_id].append(ahora)



# --- FUNCIONES DE SEGURIDAD Y NOTIFICACIÓN ---
def cifrar(texto: str) -> str:
    return cipher_suite.encrypt(texto.encode()).decode()

def descifrar(texto_cifrado: str) -> str:
    return cipher_suite.decrypt(texto_cifrado.encode()).decode()

def notificar_admin_por_telegram(email_cliente):
    mi_chat_id  = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "1420090901")
    mi_bot_token = os.getenv("TELEGRAM_TOKEN_DEFAULT", "")
    mensaje = f"🚀 *NUEVO BUZÓN VINCULADO*\n\nSe ha conectado el correo: `{email_cliente}` a un panel de control."
    url = f"https://api.telegram.org/bot{mi_bot_token}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": mi_chat_id, "text": mensaje, "parse_mode": "Markdown"})
    except:
        pass


# ==========================================
# RUTAS DE AUTENTICACIÓN (CALLBACKS)
# ==========================================

# --- MICROSOFT ---
@app.get("/api/auth/microsoft/login")
def microsoft_login(cliente_id: str):
    scopes = ["https://graph.microsoft.com/Mail.Read", "User.Read"] 
    auth_url = msal_app.get_authorization_request_url(
        scopes, 
        redirect_uri=os.getenv("MICROSOFT_REDIRECT_URI"),
        prompt="select_account",
        state=cliente_id  # MANDAMOS EL ID DE STREAMLIT A MICROSOFT
    )
    return RedirectResponse(auth_url)


@app.get("/api/auth/microsoft/callback")
def microsoft_callback(code: str = None, state: str = None, error: str = None, error_description: str = None):
    # EL PARÁMETRO "state" CONTIENE EL ID DE LA CUENTA MAESTRA
    if error:
        return {"Error de Microsoft": error, "Detalle": error_description}
        
    if not state:
        return {"error": "No se recibió el ID de la cuenta maestra (state)."}

    # 1. Intercambiamos código por tokens
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=["https://graph.microsoft.com/Mail.Read", "User.Read"],
        redirect_uri=os.getenv("MICROSOFT_REDIRECT_URI")
    )
    
    if "error" in result:
        return {"error": result.get("error_description")}
    
    access_token = result.get("access_token")
    refresh_token = result.get("refresh_token")
    
    # 2. Obtenemos el correo que se acaba de autorizar
    headers = {'Authorization': f'Bearer {access_token}'}
    user_info = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers).json()
    
    email_nuevo_buzon = user_info.get("mail") or user_info.get("userPrincipalName")

    if not email_nuevo_buzon:
        return {"error": "Microsoft no envió el correo."}

    # 3. Guardar SOLO en buzones_monitoreados apuntando al state (cliente_id)
    try:
        token_cifrado = cifrar(refresh_token) if refresh_token else None
        
        supabase.table("buzones_monitoreados").upsert({
            "cliente_id": state, # ASOCIACIÓN CORRECTA A LA CUENTA MAESTRA
            "email_cuenta": email_nuevo_buzon,
            "proveedor": "microsoft",
            "refresh_token_cifrado": token_cifrado,
            "estado_proteccion": "inactivo"
        }, on_conflict="cliente_id, email_cuenta").execute()
        
        notificar_admin_por_telegram(email_nuevo_buzon)
        
    except Exception as e:
        print(f"Error en la base de datos: {e}")
        return {"error": "No se pudo vincular el buzón", "detalle": str(e)}

    # Redirige de vuelta al dashboard de Streamlit
    #return RedirectResponse("http://localhost:8501?status=success")
    return RedirectResponse(os.getenv("DASHBOARD_URL", "http://localhost:8501") + "?status=success")


# --- GOOGLE ---
@app.get("/api/auth/google/callback")
async def google_callback(code: str, state: str = None):
    # EL PARÁMETRO "state" CONTIENE EL ID DE LA CUENTA MAESTRA
    if not state:
        return {"error": "No se recibió el ID de la cuenta maestra (state)."}
        
    # 1. Cambiar código por Tokens
    token_url = "https://oauth2.googleapis.com/token"
    datos = {
        "code": code,
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback"),
        "grant_type": "authorization_code"
    }
    
    async with httpx.AsyncClient() as client:
        respuesta = await client.post(token_url, data=datos)
        tokens = respuesta.json()
        
    if "error" in tokens:
        raise HTTPException(status_code=400, detail=f"Error con Google: {tokens}")
        
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    
    # 2. Pedir perfil para saber qué correo vinculó
    perfil_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    cabeceras = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        respuesta_perfil = await client.get(perfil_url, headers=cabeceras)
        perfil = respuesta_perfil.json()
        
    email_nuevo_buzon = perfil.get("email")

    if not email_nuevo_buzon:
        return {"error": "Google no envió el correo."}

    # 3. Guardar SOLO en buzones_monitoreados apuntando al state (cliente_id)
    try:
        if refresh_token:
            token_cifrado = cifrar(refresh_token)
            
            supabase.table("buzones_monitoreados").upsert({
                "cliente_id": state, # ASOCIACIÓN CORRECTA A LA CUENTA MAESTRA
                "email_cuenta": email_nuevo_buzon,
                "proveedor": "google",
                "refresh_token_cifrado": token_cifrado,
                "estado_proteccion": "inactivo"
            }, on_conflict="cliente_id, email_cuenta").execute()
            
            notificar_admin_por_telegram(email_nuevo_buzon)
            
    except Exception as e:
        print(f"❌ Error crítico en base de datos: {e}")
        return {"error": "Error al registrar el buzón", "detalle": str(e)}

    # Redirige de vuelta al dashboard de Streamlit
    #return RedirectResponse("http://localhost:8501?status=success")
    return RedirectResponse(os.getenv("DASHBOARD_URL", "http://localhost:8501") + "?status=success")


# ==========================================
# RUTAS DE SISTEMA (LEGACY / WEBHOOKS)
# ==========================================

@app.get("/")
def home():
    return {"mensaje": "Vigilante API Local Funcionando Correctamente 🛡️"}


#@app.get("/status")
@app.api_route("/status", methods=["GET", "HEAD"])
def status():
    """
    Página de estado del sistema — accesible públicamente.
    Muestra buzones activos, clientes y estado general.
    """
    try:
        res_buzones = supabase.table("buzones_monitoreados").select(
            "estado_proteccion, proveedor"
        ).execute()
        buzones = res_buzones.data or []

        res_clientes = supabase.table("clientes").select(
            "estado_suscripcion"
        ).execute()
        clientes = res_clientes.data or []

        activos         = sum(1 for b in buzones if b["estado_proteccion"] == "activo")
        token_expirado  = sum(1 for b in buzones if b["estado_proteccion"] == "token_expirado")
        google_count    = sum(1 for b in buzones if b["proveedor"] == "google")
        ms_count        = sum(1 for b in buzones if b["proveedor"] == "microsoft")
        clientes_activos = sum(1 for c in clientes if c["estado_suscripcion"] == "activo")

        return {
            "status":            "operational" if activos > 0 else "degraded",
            "clientes_activos":  clientes_activos,
            "buzones": {
                "total":          len(buzones),
                "activos":        activos,
                "token_expirado": token_expirado,
                "google":         google_count,
                "microsoft":      ms_count
            },
            "api": "online",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    except Exception as e:
        return {
            "status": "error",
            "detalle": str(e)
        }

# (Las rutas POST de registro viejo las hemos mantenido por si tu script en segundo plano las usa, 
# pero ya no afectan a la asociación de cuentas en el Dashboard)


class RegistroAmenaza(BaseModel):
    usuario_email: str 
    id_mensaje_correo: str
    remitente: str
    asunto: str
    veredicto_ia: str
    links_sospechosos: str = "Ninguno"
    carpeta: str = "INBOX"

@app.post("/api/amenazas/registrar")
def registrar_amenaza(amenaza: RegistroAmenaza):
    # Lógica antigua mantenida por compatibilidad
    pass

class LoginData(BaseModel):
    email: str
    password: str


@app.post("/api/auth/verify")
def verify_login(data: LoginData, request: Request):
    """Verifica credenciales para la PWA móvil y registra el acceso"""
    try:
        res = supabase.table("clientes").select(
            "id, nombre, password_hash"
        ).eq("email_principal", data.email).execute()

        if not res.data:
            # Registrar intento fallido
            supabase.table("audit_logs").insert({
                "email": data.email,
                "accion": "LOGIN_FALLIDO",
                "ip": request.client.host,
                "detalle": "Usuario no encontrado"
            }).execute()
            raise HTTPException(status_code=401, detail="Usuario no encontrado")

        usuario = res.data[0]
        hash_guardado = usuario.get("password_hash") or ""

        try:
            ok = bcrypt.checkpw(
                data.password.encode("utf-8"),
                hash_guardado.encode("utf-8")
            )
        except Exception:
            ok = False

        if not ok:
            # Registrar intento fallido
            supabase.table("audit_logs").insert({
                "cliente_id": usuario["id"],
                "email": data.email,
                "accion": "LOGIN_FALLIDO",
                "ip": request.client.host,
                "detalle": "Contraseña incorrecta"
            }).execute()
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")

        # Registrar login exitoso
        supabase.table("audit_logs").insert({
            "cliente_id": usuario["id"],
            "email": data.email,
            "accion": "LOGIN_OK",
            "ip": request.client.host,
            "detalle": "Acceso desde PWA móvil"
        }).execute()

        return {
            "ok": True,
            "id": usuario["id"],
            "nombre": usuario["nombre"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pwa/dashboard/{cliente_id}")
def pwa_dashboard(cliente_id: str):
    check_rate_limit(cliente_id)
    """Datos del dashboard para la PWA móvil"""
    try:
        # Buzones del cliente
        res_buz = supabase.table("buzones_monitoreados").select("*").eq("cliente_id", cliente_id).execute()
        buzones = res_buz.data or []
        ids_buzones = [b["id"] for b in buzones]

        # Amenazas de hoy
        from datetime import datetime, timezone
        hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        amenazas = []
        if ids_buzones:
            res_am = supabase.table("amenazas_detectadas").select(
                "veredicto_ia, remitente, asunto, fecha_analisis, carpeta"
            ).in_("buzon_id", ids_buzones).gte("fecha_analisis", hoy).order(
                "fecha_analisis", desc=True
            ).limit(20).execute()
            amenazas = res_am.data or []

        criticos = sum(1 for a in amenazas if a["veredicto_ia"] == "CRÍTICO")
        altos    = sum(1 for a in amenazas if a["veredicto_ia"] == "ALTO")
        medios   = sum(1 for a in amenazas if a["veredicto_ia"] == "MEDIO")
        bajos    = sum(1 for a in amenazas if a["veredicto_ia"] == "BAJO")

        return {
            "buzones": buzones,
            "amenazas": amenazas,
            "stats": {
                "total": len(amenazas),
                "criticos": criticos,
                "altos": altos,
                "medios": medios,
                "bajos": bajos
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

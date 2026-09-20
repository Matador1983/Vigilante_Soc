import streamlit as st
from supabase import create_client
import pandas as pd
import os
import bcrypt
import plotly.express as px    
from dotenv import load_dotenv
from datetime import datetime






# --- CONFIGURACIÓN INICIAL ---
st.set_page_config(page_title="Vigilante SOC Panel", layout="wide", initial_sidebar_state="expanded")
load_dotenv()

# Inicializamos las variables de sesión
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if "usuario_nombre" not in st.session_state:
    st.session_state.usuario_nombre = ""
if "usuario_id" not in st.session_state:
    st.session_state.usuario_id = None
if "email_usuario" not in st.session_state:
    st.session_state.email_usuario = ""
# Rate limiting: máximo 5 intentos fallidos antes de bloquear
if "login_intentos" not in st.session_state:
    st.session_state.login_intentos = 0
if "login_bloqueado" not in st.session_state:
    st.session_state.login_bloqueado = False

# Conexión a Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --- DISEÑO VISUAL ---
def cargar_fondo_cyberseguridad():
    url_imagen = "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?q=80&w=2070&auto=format&fit=crop"
    css_fondo = f"""
    <style>
    .stApp {{
        background-image: linear-gradient(rgba(14, 17, 23, 0.85), rgba(14, 17, 23, 0.95)), url("{url_imagen}");
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        background-attachment: fixed;
    }}
    [data-testid="stVerticalBlockBorderWrapper"] {{
        border: 1px solid rgba(6, 214, 160, 0.2) !important;
        box-shadow: 0 4px 30px rgba(0, 0, 0, 0.5);
    }}
    </style>
    """
    st.markdown(css_fondo, unsafe_allow_html=True)

cargar_fondo_cyberseguridad()

# ==========================================
# FLUJO DE ACCESO Y REGISTRO
# ==========================================
if not st.session_state.autenticado:
    col_izq, col_centro, col_der = st.columns([1, 1.5, 1])
    
    with col_centro:
        st.markdown("<h1 style='text-align: center;'>🛡️ Vigilante SOC</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; font-size: 20px; color: #8d99ae;'>Tu sala de control de ciberseguridad impulsada por IA.</p>", unsafe_allow_html=True)
        st.write("") 
        
        
        
     # --- CSS ESPECÍFICO SOLO PARA TABS ---
        st.markdown("""
    <style>
    /* Buscamos el contenedor de texto específico de los botones de los tabs */
    .stTabs [data-testid="stMarkdownContainer"] p {
        font-size: 20px !important; /* Tamaño grande solo para los títulos de los tabs */
        font-weight: normal!important;
    }

    /* Opcional: Asegura que los emojis de los tabs también se vean grandes */
    .stTabs [data-testid="stMarkdownContainer"] {
        line-height: 1.5;
    }
    /* Estilo para que la pestaña seleccionada resalte más */
    button[aria-selected="true"] p {
        color: #00FF00 !important; /* Verde Neón para la pestaña activa */
    }
    </style>
    """, unsafe_allow_html=True)
        
        
        
        tab_login, tab_registro = st.tabs(["🔐 Iniciar Sesión", "📝 Crear Cuenta Maestra"])
        
        
        # --- LOGIN ---
        with tab_login:
            with st.container(border=True):

                # Rate limiting — bloqueo tras 5 intentos fallidos
                if st.session_state.login_bloqueado:
                    st.error("🔒 Demasiados intentos fallidos. Cerrá y volvé a abrir el navegador para reintentar.")
                else:
                    intentos_restantes = 5 - st.session_state.login_intentos
                    if st.session_state.login_intentos > 0:
                        st.warning(f"⚠️ Intentos restantes: {intentos_restantes}")

                    email_input = st.text_input("Correo de acceso", placeholder="tu@correo.com", key="login_email")
                    pass_input  = st.text_input("Contraseña", type="password", key="login_pass")

                    if st.button("Entrar al Panel", type="primary", use_container_width=True):
                        if not email_input or not pass_input:
                            st.error("⚠️ Ingresá tu correo y contraseña.")
                        else:
                            # Buscar usuario por email (sin comparar contraseña en Supabase)
                            res = supabase.table("clientes").select("id, nombre, password_hash").eq(
                                "email_principal", email_input
                            ).execute()

                            autenticado = False
                            if res.data:
                                usuario      = res.data[0]
                                hash_guardado = usuario.get("password_hash") or ""
                                # bcrypt.checkpw compara la contraseña con el hash guardado
                                try:
                                    autenticado = bcrypt.checkpw(
                                        pass_input.encode("utf-8"),
                                        hash_guardado.encode("utf-8")
                                    )
                                except Exception:
                                    autenticado = False

                            if autenticado:
                                st.session_state.autenticado    = True
                                st.session_state.usuario_id     = usuario['id']
                                st.session_state.usuario_nombre = usuario.get('nombre', email_input)
                                st.session_state.email_usuario  = email_input
                                st.session_state.login_intentos = 0
                                st.rerun()
                            else:
                                st.session_state.login_intentos += 1
                                if st.session_state.login_intentos >= 5:
                                    st.session_state.login_bloqueado = True
                                    st.error("🔒 Cuenta bloqueada por demasiados intentos.")
                                else:
                                    st.error("❌ Correo o contraseña incorrectos.")
                            

        # --- REGISTRO ---
        with tab_registro:
            with st.container(border=True):
                st.markdown("<h4 style='text-align: center;'>Crea tu Identidad</h4>", unsafe_allow_html=True)
                
               
                st.markdown(""" 
                <style>
                /* Oculta la etiqueta (label) de todos los inputs para que no se duplique el texto */
                label[data-testid="stWidgetLabel"] {
                    display: none;
                }
                /* Agranda el texto que el usuario escribe dentro del cuadro */
                .stTextInput input {
                    font-size: 18px !important;
                }
                </style>
                """, unsafe_allow_html=True)
               
                
                
                st.markdown('<p style=" color: #FFFFFF; text-align: left; margin-bottom: -10px;">Tu Nombre Completo</p>', unsafe_allow_html=True)
                st.markdown("")
                # 3. El input captura el valor (la etiqueta se pone pero el CSS la oculta)
                nuevo_nombre = st.text_input("LabelOculto1", key="input_nombre")

                st.markdown('<p style=" color: #FFFFFF; text-align: left; margin-bottom: -10px; margin-top: 15px;">Correo Principal (Será tu ID de acceso)</p>', unsafe_allow_html=True)
                st.markdown("")
                nuevo_email = st.text_input("LabelOculto2", key="input_email")

                st.markdown('<p style=" color: #FFFFFF; text-align: left; margin-bottom: -10px; margin-top: 15px;">Contraseña</p>', unsafe_allow_html=True)
                st.markdown("")
                nueva_pass  = st.text_input("LabelOculto3", type="password", key="input_pass")

                st.markdown('<p style=" color: #FFFFFF; text-align: left; margin-bottom: -10px; margin-top: 15px;">Repetir Contraseña</p>', unsafe_allow_html=True)
                st.markdown("")
                nueva_pass2 = st.text_input("LabelOculto4", type="password", key="input_pass2")
                 
                #nuevo_nombre = st.text_input("Tu Nombre Completo")
                #nuevo_email = st.text_input("Correo Principal (Será tu ID de acceso)")
                
                st.markdown(""" <p style=" color: #FFFFF; text-align: left; font-style: normal;">
                                        Selecciona tu nivel de Plan de Suscripción:</p>  """, unsafe_allow_html=True)                     
                
                # --- SISTEMA DE SELECCIÓN DE PLANES CON MEMORIA ---
               # 1. Inicializamos la memoria de selección (esto va arriba en tu código)
                if "plan_seleccionado" not in st.session_state:
                    st.session_state.plan_seleccionado = None
                    st.session_state.limite_seleccionado = 0
                    st.session_state.opcion_plan = ""


                col1, col2, col3 = st.columns(3)

                # --- BLOQUE PLAN 1 ---
                with col1:
                    with st.container(border=True):
                        # Si este plan es el que está en memoria, mostramos un borde o estilo distinto
                        estilo_1 = "primary" if st.session_state.plan_seleccionado == "Esencial" else "secondary"
                        
                        if st.button("Plan Esencial", use_container_width=True, key="btn_esencial", type=estilo_1):
                            st.session_state.plan_seleccionado = "Esencial"
                            st.session_state.limite_seleccionado = 1
                            st.session_state.opcion_plan = "Individual"
                            st.rerun() # Forzamos recarga para que los otros botones se "apaguen"
                        st.markdown(""" <p style=" color: #FFFFF; text-align: center; font-style: normal;">
                                        Protección para 1 cuenta  </p>  """, unsafe_allow_html=True)    
                        st.markdown(""" <p style=" color: #FFFFF; text-align: center; font-style: italic;">
                                        Precio 5$ / mes  </p>  """, unsafe_allow_html=True)    
                        
                        if st.session_state.plan_seleccionado == "Esencial":
                            st.markdown("<p style='color:#00ff00;  text-align:center;'>✅ Seleccionado</p>", unsafe_allow_html=True)

                # --- BLOQUE PLAN 2 ---
                with col2:
                    with st.container(border=True):
                        estilo_2 = "primary" if st.session_state.plan_seleccionado == "Pro" else "secondary"
                        
                        if st.button("Plan Pro", use_container_width=True, key="btn_pro", type=estilo_2):
                            st.session_state.plan_seleccionado = "Pro"
                            st.session_state.limite_seleccionado = 5
                            st.session_state.opcion_plan = "Profesional"
                            st.rerun()
                        
                        st.markdown(""" <p style="color: #FFFFF; text-align: center; font-style: italic;">
                                        Protección para 5 cuenta  </p>  """, unsafe_allow_html=True)    
                        st.markdown(""" <p style=" color: #FFFFF; text-align: center; font-style: italic;">
                                        Precio 10$ / mes  </p>  """, unsafe_allow_html=True)  
                        
                        if st.session_state.plan_seleccionado == "Pro":
                            st.markdown("<p style='color:#00ff00; text-align:center;'>✅ Seleccionado</p>", unsafe_allow_html=True)

                # --- BLOQUE PLAN 3 ---
                with col3:
                    with st.container(border=True):
                        estilo_3 = "primary" if st.session_state.plan_seleccionado == "Business" else "secondary"
                        
                        if st.button("Plan Business", use_container_width=True, key="btn_business", type=estilo_3):
                            st.session_state.plan_seleccionado = "Business"
                            st.session_state.limite_seleccionado = 20
                            st.session_state.opcion_plan = "Corporativo"
                            st.rerun()
                        st.markdown(""" <p style="color: #FFFFF; text-align: center; font-style: italic;">
                                        Protección para 10 cuenta  </p>  """, unsafe_allow_html=True)    
                        st.markdown(""" <p style=" color: #FFFFF; text-align: center; font-style: italic;">
                                        Precio 20$ / mes  </p>  """, unsafe_allow_html=True)  
                        
                        if st.session_state.plan_seleccionado == "Business":
                            st.markdown("<p style='color:#00ff00;  text-align:center;'>✅ Seleccionado</p>", unsafe_allow_html=True)

                # --- VALIDACIÓN FINAL ---
                st.write("---")
                if st.session_state.plan_seleccionado:
                    st.info(f"📍 Has elegido el **Plan {st.session_state.plan_seleccionado}** ({st.session_state.limite_seleccionado} cuentas)")
                else:
                    st.warning("⚠️ Por favor, selecciona un plan para continuar con el registro.")
    
    
    
                st.write("---")
                
                # --- BOTÓN FINAL DE REGISTRO ---
                # Se ejecuta solo si hace clic aquí, usando los datos guardados en memoria
                if st.button("Crear Cuenta Maestra", type="primary", use_container_width=True):
                    if not nuevo_nombre or not nuevo_email:
                        st.error("⚠️ El nombre y el correo son obligatorios.")
                    elif not nueva_pass or len(nueva_pass) < 6:
                        st.error("⚠️ La contraseña debe tener al menos 6 caracteres.")
                    elif nueva_pass != nueva_pass2:
                        st.error("⚠️ Las contraseñas no coinciden.")
                    elif not st.session_state.plan_seleccionado:
                        st.error("⚠️ Debes seleccionar un plan antes de continuar.")
                    else:
                        existe = supabase.table("clientes").select("id").eq("email_principal", nuevo_email).execute()
                        if existe.data:
                            st.warning("Este correo ya está registrado. Ve a la pestaña 'Iniciar Sesión'.")
                        else:
                            pass_hash = bcrypt.hashpw(nueva_pass.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                            nuevo_usuario = {
                                "nombre":           nuevo_nombre,
                                "email_principal":  nuevo_email,
                                "password_hash":    pass_hash,
                                "plan_tipo":        st.session_state.opcion_plan,
                                "limite_buzones":   st.session_state.limite_seleccionado,
                                "estado_suscripcion": "inactivo"
                            }
                            try:
                                supabase.table("clientes").insert(nuevo_usuario).execute()
                                st.success("✅ ¡Cuenta creada con éxito! Ahora puedes Iniciar Sesión en la pestaña de al lado.")
                                st.session_state.plan_seleccionado = None
                            except Exception as e:
                                st.error(f"Error en la base de datos: {e}")
# ==========================================
# DASHBOARD PRIVADO
# ==========================================
else:
    id_actual = st.session_state.usuario_id
    
    # 1. Datos de la cuenta maestra
    c_res = supabase.table("clientes").select("*").eq("id", id_actual).single().execute()
    cliente = c_res.data if c_res.data else {}
    
    # 2. Datos de los buzones asociados
    res_buzones = supabase.table("buzones_monitoreados").select("*").eq("cliente_id", id_actual).execute()
    buzones_data = res_buzones.data if res_buzones.data else []
    conteo_actual = len(buzones_data)

    # --- BARRA LATERAL (GESTIÓN Y CONFIGURACIÓN) ---
    with st.sidebar:
        st.header("⚙️ Configuración")
        st.write(f"Usuario: **{st.session_state.usuario_nombre}**")
        st.markdown("---")
        
        # --- SUSCRIPCIÓN Y LÍMITES ---
        st.subheader("🛡️ Tu Suscripción")
        plan_actual = cliente.get('plan_tipo', 'Individual')
        limite_max = cliente.get('limite_buzones', 1)
        
        st.write(f"**Plan:** {plan_actual}")
        progreso = min(conteo_actual / limite_max, 1.0) if limite_max > 0 else 0
        st.progress(progreso)
        st.caption(f"Capacidad: {conteo_actual} de {limite_max} buzones vinculados")
        
        with st.expander("🚀 Cambiar o Mejorar Plan"):
            st.info("Al cambiar de plan, tus límites se actualizarán de inmediato.")
            nuevo_plan_sel = st.selectbox(
                "Elegir nuevo plan:",
                options=["Individual", "Profesional", "Enterprise"],
                index=["Individual", "Profesional", "Enterprise"].index(plan_actual)
            )
            
            if nuevo_plan_sel != plan_actual:
                if st.button("Confirmar Cambio", use_container_width=True, type="primary"):
                    nuevos_limites = {"Individual": 1, "Profesional": 5, "Enterprise": 20}
                    supabase.table("clientes").update({
                        "plan_tipo": nuevo_plan_sel,
                        "limite_buzones": nuevos_limites[nuevo_plan_sel]
                    }).eq("id", id_actual).execute()
                    st.success(f"¡Cambiado a {nuevo_plan_sel}!")
                    st.rerun()
        
        st.markdown("---")
        
        # --- VINCULAR NUEVAS CUENTAS ---
        if conteo_actual < limite_max:
            st.subheader("➕ Vincular Buzón")
            st.info("Añade cuentas para que la IA comience a vigilarlas.")
            
            # Botón Gmail
            client_id_google = os.getenv("GOOGLE_CLIENT_ID")
            #redirect_uri = "http://127.0.0.1.nip.io:8000/api/auth/google/callback"
            redirect_uri = "https://wish-expected-trembl-leaves.trycloudflare.com/api/auth/google/callback"
            
            link_google = (
                f"https://accounts.google.com/o/oauth2/v2/auth?"
                f"client_id={client_id_google}&" 
                f"redirect_uri={redirect_uri}&"
                f"response_type=code&"
                f"scope=https://www.googleapis.com/auth/userinfo.email%20https://mail.google.com/&"
                f"access_type=offline&"
                f"prompt=consent&"
                f"state={id_actual}" 
            )
                    
            if st.button("🌐 Añadir Gmail", use_container_width=True):
                st.markdown(f'<meta http-equiv="refresh" content="0;URL=\'{link_google}\'">', unsafe_allow_html=True)
            
            # Botón Microsoft
            #link_microsoft = f"http://localhost:8000/api/auth/microsoft/login?cliente_id={id_actual}"
            link_microsoft = f"https://wish-expected-trembl-leaves.trycloudflare.com/api/auth/microsoft/login?cliente_id={id_actual}"
            if st.button("🟦 Añadir Outlook", use_container_width=True):
                st.markdown(f'<meta http-equiv="refresh" content="0;URL=\'{link_microsoft}\'">', unsafe_allow_html=True)
        else:
            st.error("🚫 Has alcanzado el límite de correos de tu plan.")
            st.button("🚀 Mejorar Plan", use_container_width=True, disabled=True)
           
        st.markdown("---")
        
        # --- ALERTAS TELEGRAM ---
        st.subheader("Alertas Telegram")
        with st.expander("📲 Configurar Notificaciones"):
            st.write("Recibe avisos inmediatos cuando la IA detecte una amenaza crítica.")
            chat_actual = cliente.get('telegram_chat_id', '') or ''
            bot_actual = cliente.get('telegram_bot_token', '') or ''
            
            nuevo_chat_id = st.text_input("Tu Telegram Chat ID", value=chat_actual)
            nuevo_bot_token = st.text_input("Tu Bot Token (Opcional)", value=bot_actual, type="password")
            
            if st.button("Guardar Configuración", type="primary", use_container_width=True):
                supabase.table("clientes").update({
                    "telegram_chat_id": nuevo_chat_id,
                    "telegram_bot_token": nuevo_bot_token
                }).eq("id", id_actual).execute()
                st.success("¡Configuración guardada!")
   
        st.markdown("---")
        if st.button("🚪 Cerrar Sesión", use_container_width=True):
            st.session_state.autenticado = False
            st.rerun()

    # --- PANEL PRINCIPAL (DASHBOARD) ---
    if not cliente:
        st.warning("Error cargando perfil.")
    else:
        st.title(f"🛡️ Central de Vigilancia SOC")
        
        # Validar Vencimiento
        vencimiento_str = cliente.get('fecha_vencimiento')
        if vencimiento_str:
            vencimiento = datetime.fromisoformat(vencimiento_str.replace('Z', '+00:00'))
            hoy = datetime.now(vencimiento.tzinfo)
            dias_restantes = (vencimiento - hoy).days
            
            if 0 < dias_restantes < 5:  
                st.warning(f"⚠️ Tienes **{dias_restantes} días** de protección premium restantes. Tu servicio podría suspenderse pronto.")
            elif dias_restantes > 5:
                st.info(f"💡 Tienes **{dias_restantes} días** de protección premium restantes.")
            else:
                st.error("⚠️ Tu suscripción ha vencido. El patrullaje está pausado. Contacta con soporte.")
        
        st.divider()

        if conteo_actual == 0:
            st.info("Aún no tienes correos asociados. Usa la barra lateral (➕ Vincular Buzón) para conectar tu primera cuenta.")
        else:
            # --- MIS BUZONES (VISTA RESUMIDA) ---
            st.subheader("📫 Buzones bajo protección")

            # Links de reconexión
            client_id_google    = os.getenv("GOOGLE_CLIENT_ID")
            redirect_uri_google = os.getenv("GOOGLE_REDIRECT_URI", "https://wish-expected-trembl-leaves.trycloudflare.com/api/auth/google/callback")
            link_reconectar_google = (
                f"https://accounts.google.com/o/oauth2/v2/auth?"
                f"client_id={client_id_google}&"
                f"redirect_uri={redirect_uri_google}&"
                f"response_type=code&"
                f"scope=https://www.googleapis.com/auth/userinfo.email%20https://mail.google.com/&"
                f"access_type=offline&prompt=consent&state={id_actual}"
            )
            link_reconectar_ms = f"https://wish-expected-trembl-leaves.trycloudflare.com/api/auth/microsoft/login?cliente_id={id_actual}"

            cols_buzones = st.columns(3)
            for i, b in enumerate(buzones_data):
                with cols_buzones[i % 3]:
                    ep        = b['estado_proteccion']
                    email_buz = b['email_cuenta']
                    prov      = b['proveedor'].upper()
                    if ep == 'activo':
                        st.info(f"**{email_buz}**\n\nProveedor: {prov}\n\nEstado: ✅ Activo")
                    elif ep == 'token_expirado':
                        st.error(f"**{email_buz}**\n\nProveedor: {prov}\n\nEstado: 🔒 Token vencido")
                        link_rec = link_reconectar_google if b['proveedor'] == 'google' else link_reconectar_ms
                        color    = "#1a73e8" if b['proveedor'] == 'google' else "#0078d4"
                        label    = "🔄 Reconectar Gmail" if b['proveedor'] == 'google' else "🔄 Reconectar Outlook"
                        st.markdown(
                            f'<a href="{link_rec}" target="_self">'
                            f'<button style="width:100%;background:{color};color:white;border:none;'
                            f'padding:8px;border-radius:6px;cursor:pointer;font-size:14px;">{label}</button></a>',
                            unsafe_allow_html=True
                        )
                    else:
                        st.warning(f"**{email_buz}**\n\nProveedor: {prov}\n\nEstado: ❌ Pausado")
                    
                    
                    
                      # =====================================================================
            # 🗑️ NUEVA SECCIÓN: DESVINCULAR / ELIMINAR CUENTA DE MONITOREO
            # =====================================================================
            with st.expander("⚙️ Administrar / Eliminar un buzón vinculado"):
                st.markdown("<p style='color: #8d99ae;'>Selecciona la cuenta que deseas desvincular del monitoreo del SOC. Esta acción eliminará el patrullaje de la IA sobre este buzón.</p>", unsafe_allow_html=True)
                
                # Generamos la lista de correos actuales del cliente para el selector
                lista_correos_eliminar = [b['email_cuenta'] for b in buzones_data]
                
                cuenta_seleccionada = st.selectbox(
                    "Selecciona el correo a eliminar:",
                    options=lista_correos_eliminar,
                    index=None,
                    placeholder="Elige una cuenta de correo..."
                )
                
                if cuenta_seleccionada:
                    # Buscamos el ID del buzón correspondiente al correo seleccionado
                    buzon_a_eliminar = next((b for b in buzones_data if b['email_cuenta'] == cuenta_seleccionada), None)
                    
                    if buzon_a_eliminar:
                        st.warning(f"⚠️ ¿Estás seguro de que deseas eliminar **{cuenta_seleccionada}**? Se detendrá el análisis de amenazas.")
                        
                        # Botón de confirmación definitiva
                        if st.button(f"🚨 Confirmar Eliminación de {cuenta_seleccionada}", type="primary", use_container_width=True):
                            try:
                                # Ejecutamos el DELETE en Supabase apuntando al ID único del buzón
                                supabase.table("buzones_monitoreados").delete().eq("id", buzon_a_eliminar['id']).execute()
                                
                                st.success(f"✅ La cuenta {cuenta_seleccionada} ha sido eliminada con éxito.")
                                # Forzamos recarga de Streamlit para actualizar las métricas y la interfaz inmediatamente
                                st.rerun()
                                
                            except Exception as e:
                                st.error(f"Error al intentar eliminar el buzón de la base de datos: {e}")
            # =====================================================================
                    
                    

            st.divider()
            st.subheader("🚨 Inteligencia de Amenazas Global")
            
            # --- OBTENCIÓN Y ANÁLISIS DE AMENAZAS ---
            lista_ids_buzones = [b['id'] for b in buzones_data]
            res_amenazas = supabase.table("amenazas_detectadas").select("*").in_("buzon_id", lista_ids_buzones).execute()
            df = pd.DataFrame(res_amenazas.data)
            
            if df.empty:
                st.success("🎉 ¡Todas tus bandejas están limpias! Aún no hay amenazas registradas.")
            else:
                if 'carpeta' not in df.columns:
                    df['carpeta'] = "INBOX"

                # Filtros en la barra lateral
                st.sidebar.markdown("---")
                st.sidebar.header("🎛️ Filtros de Análisis")
                carpeta_filtro = st.sidebar.multiselect("Filtrar por Carpeta:", options=df['carpeta'].unique(), default=df['carpeta'].unique())
                df_filtrado = df[df['carpeta'].isin(carpeta_filtro)]

                # ── KPIs completos con todos los niveles ──────────────
                hoy_inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

                # Totales históricos
                total_historico  = len(df_filtrado)
                criticos_total   = len(df_filtrado[df_filtrado['veredicto_ia'] == 'CRÍTICO'])
                altos_total      = len(df_filtrado[df_filtrado['veredicto_ia'] == 'ALTO'])
                medios_total     = len(df_filtrado[df_filtrado['veredicto_ia'] == 'MEDIO'])
                bajos_total      = len(df_filtrado[df_filtrado['veredicto_ia'] == 'BAJO'])

                # Solo de hoy
                if 'fecha_analisis' in df_filtrado.columns:
                    df_filtrado['fecha_analisis'] = pd.to_datetime(df_filtrado['fecha_analisis'], utc=True)
                    hoy_utc   = pd.Timestamp.now(tz='UTC').replace(hour=0, minute=0, second=0, microsecond=0)
                    df_hoy    = df_filtrado[df_filtrado['fecha_analisis'] >= hoy_utc]
                    total_hoy = len(df_hoy)
                else:
                    total_hoy = 0

                # Fila 1 — resumen del día
                st.markdown("**📅 Actividad de hoy**")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("📧 Analizados hoy", total_hoy)
                col2.metric("🚨 Críticos hoy",   len(df_hoy[df_hoy['veredicto_ia'] == 'CRÍTICO']) if total_hoy > 0 else 0)
                col3.metric("⚠️ Altos hoy",      len(df_hoy[df_hoy['veredicto_ia'] == 'ALTO'])    if total_hoy > 0 else 0)
                col4.metric("📫 Buzones activos", len(lista_ids_buzones))

                st.markdown("**📊 Histórico total**")
                col5, col6, col7, col8 = st.columns(4)
                col5.metric("📧 Total analizados", total_historico)
                col6.metric("🚨 Críticos",  criticos_total,
                            delta=f"{criticos_total} detectados" if criticos_total > 0 else "Ninguno",
                            delta_color="inverse")
                col7.metric("🟡 Medios",    medios_total)
                col8.metric("🟢 Bajos",     bajos_total)

                st.divider()

                # ── Gráfica + Vista agrupada ──────────────────────────
                col_graf, col_tabla = st.columns([1, 1.5])

                with col_graf:
                    st.markdown("**Distribución de Riesgo**")
                    colores_amenazas = {"CRÍTICO": "#ff2b2b", "ALTO": "#ff8a00", "MEDIO": "#ffd166", "BAJO": "#06d6a0", "ERROR_IA": "#8d99ae"}
                    df_counts = df_filtrado['veredicto_ia'].value_counts().reset_index()
                    df_counts.columns = ['Veredicto', 'Cantidad']
                    fig = px.pie(df_counts, values='Cantidad', names='Veredicto', color='Veredicto',
                                 color_discrete_map=colores_amenazas, hole=0.4)
                    fig.update_layout(showlegend=True)
                    st.plotly_chart(fig, use_container_width=True)

                # ── Vista agrupada por tipo de amenaza (inspirada en SOC-Lab) ──
                with col_tabla:
                    st.markdown("**📋 Amenazas Agrupadas**")

                    # Orden de severidad para mostrar las más graves primero
                    orden_severidad = ["CRÍTICO", "ALTO", "MEDIO", "BAJO", "ERROR_IA"]
                    iconos_nivel    = {"CRÍTICO": "🚨", "ALTO": "⚠️", "MEDIO": "🟡", "BAJO": "🟢", "ERROR_IA": "⚙️"}

                    # Agrupar por (veredicto + remitente + asunto) para detectar patrones repetidos
                    df_filtrado['grupo'] = (
                        df_filtrado['veredicto_ia'] + "||" +
                        df_filtrado['remitente'].str[:50] + "||" +
                        df_filtrado['asunto'].str[:50]
                    )
                    df_agrupado = (
                        df_filtrado.groupby('grupo')
                        .agg(
                            veredicto  = ('veredicto_ia', 'first'),
                            remitente  = ('remitente', 'first'),
                            asunto     = ('asunto', 'first'),
                            carpeta    = ('carpeta', 'first'),
                            count      = ('id', 'count'),
                            ultima_vez = ('fecha_analisis', 'max')
                        )
                        .reset_index(drop=True)
                    )

                    # Ordenar por severidad y luego por fecha
                    df_agrupado['orden'] = df_agrupado['veredicto'].map(
                        {v: i for i, v in enumerate(orden_severidad)}
                    ).fillna(99)
                    df_agrupado = df_agrupado.sort_values(['orden', 'ultima_vez'], ascending=[True, False])

                    for _, row in df_agrupado.iterrows():
                        nivel    = row['veredicto']
                        icono    = iconos_nivel.get(nivel, "📧")
                        count    = int(row['count'])
                        repetido = f" ×{count}" if count > 1 else ""
                        fecha    = pd.to_datetime(row['ultima_vez']).strftime('%d/%m %H:%M') if pd.notna(row['ultima_vez']) else "—"

                        # Título del expander — una línea por grupo
                        titulo = f"{icono} [{nivel}]{repetido} — {row['remitente'][:40]} | {row['asunto'][:35]}"

                        with st.expander(titulo, expanded=(nivel in ["CRÍTICO", "ALTO"] and count == 1)):
                            col_a, col_b = st.columns([2, 1])
                            with col_a:
                                st.markdown(f"**De:** {row['remitente']}")
                                st.markdown(f"**Asunto:** {row['asunto']}")
                                st.markdown(f"**Carpeta:** `{row['carpeta']}`")
                            with col_b:
                                st.markdown(f"**Nivel:** `{nivel}`")
                                st.markdown(f"**Última vez:** {fecha}")
                                if count > 1:
                                    st.markdown(f"**Ocurrencias:** `×{count}`")

                            # Mostrar los registros individuales del grupo si hay más de uno
                            if count > 1:
                                registros_grupo = df_filtrado[
                                    (df_filtrado['veredicto_ia'] == row['veredicto']) &
                                    (df_filtrado['remitente'].str[:50] == row['remitente'][:50]) &
                                    (df_filtrado['asunto'].str[:50] == row['asunto'][:50])
                                ][['fecha_analisis', 'carpeta', 'links_sospechosos']].copy()
                                registros_grupo.columns = ['Fecha', 'Carpeta', 'Links']
                                st.dataframe(registros_grupo.sort_values('Fecha', ascending=False),
                                             use_container_width=True, hide_index=True)
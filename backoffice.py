import streamlit as st
import pandas as pd
from supabase import create_client
import os
from dotenv import load_dotenv
import plotly.express as px
from datetime import datetime, timedelta
import pyotp


# --- CONFIGURACIÓN DE PÁGINA EXCLUSIVA PARA ADMIN ---
st.set_page_config(page_title="Vigilante SOC - BACKOFFICE", layout="wide", page_icon="👑")
load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --- INYECCIÓN DE DISEÑO (FONDO CSS) ---
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

# --- BARRERA DE SEGURIDAD ---
if "admin_auth" not in st.session_state:
    st.session_state.admin_auth = False
if "admin_intentos" not in st.session_state:
    st.session_state.admin_intentos = 0
if "admin_bloqueado" not in st.session_state:
    st.session_state.admin_bloqueado = False

ADMIN_PASSWORD = os.getenv("BACKOFFICE_PASSWORD", "")

if not st.session_state.admin_auth:

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; font-size: 4em;'>👑</h1>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>Acceso Maestro</h2>", unsafe_allow_html=True)
        
        with st.container(border=True):
            if st.session_state.admin_bloqueado:
                st.error("🔒 Demasiados intentos fallidos. Cerrá y volvé a abrir el navegador.")
            else:
                if st.session_state.admin_intentos > 0:
                    restantes = 5 - st.session_state.admin_intentos
                    st.warning(f"⚠️ Intentos restantes: {restantes}")

                pwd = st.text_input("Contraseña de Administrador", type="password", placeholder="Ingresa la clave maestra...")
                codigo_totp = st.text_input("🔐 Código Google Authenticator", placeholder="123456", max_chars=6)

                if st.button("Desbloquear Sistema", use_container_width=True, type="primary"):
                    if not ADMIN_PASSWORD:
                        st.error("❌ BACKOFFICE_PASSWORD no está configurada en el .env")
                    elif pwd != ADMIN_PASSWORD:
                        st.session_state.admin_intentos += 1
                        if st.session_state.admin_intentos >= 5:
                            st.session_state.admin_bloqueado = True
                            st.error("🔒 Acceso bloqueado por demasiados intentos.")
                        else:
                            st.error("Acceso Denegado.")
                    elif not codigo_totp or len(codigo_totp) != 6:
                        st.error("⚠️ Ingresá el código de 6 dígitos del Authenticator.")
                    else:
                        totp_secret = os.getenv("TOTP_SECRET", "")
                        totp = pyotp.TOTP(totp_secret)
                        if not totp.verify(codigo_totp, valid_window=1):
                            st.session_state.admin_intentos += 1
                            if st.session_state.admin_intentos >= 5:
                                st.session_state.admin_bloqueado = True
                                st.error("🔒 Acceso bloqueado por demasiados intentos.")
                            else:
                                st.error("❌ Código del Authenticator incorrecto o expirado.")
                        else:
                            st.session_state.admin_auth     = True
                            st.session_state.admin_intentos = 0
                            st.rerun()


                
# --- PANEL DE CONTROL MAESTRO ---
else:
    col_tit, col_bot = st.columns([4, 1])
    with col_tit:
        st.title("👑 Centro de Comando (Backoffice)")
        st.markdown("Administra la base de clientes maestros y sus buzones asociados.")
    with col_bot:
        st.write("")
        if st.button("🚪 Cerrar Sesión", type="secondary", use_container_width=True):
            st.session_state.admin_auth = False
            st.rerun()
        
    st.markdown("---")

    try:
        # 1. Descargamos Datos de Ambas Tablas
        res_clientes = supabase.table("clientes").select("*").execute()
        res_buzones = supabase.table("buzones_monitoreados").select("*").execute()
        
        df_clientes = pd.DataFrame(res_clientes.data)
        df_buzones = pd.DataFrame(res_buzones.data)

        if not df_clientes.empty:
            
            # --- PREPARACIÓN DE DATOS CRUZADOS ---
            # Contamos cuántos buzones tiene cada cliente
            if not df_buzones.empty:
                conteo_por_cliente = df_buzones.groupby('cliente_id').size().reset_index(name='buzones_vinculados')
                df_clientes = pd.merge(df_clientes, conteo_por_cliente, left_on='id', right_on='cliente_id', how='left')
                df_clientes['buzones_vinculados'] = df_clientes['buzones_vinculados'].fillna(0).astype(int)
            else:
                df_clientes['buzones_vinculados'] = 0

            # --- 0. ESTADÍSTICAS RÁPIDAS Y GRÁFICA ---
            st.subheader("📈 Análisis de Crecimiento")
            col_met1, col_met2, col_met3 = st.columns(3)
            
            total_clientes = len(df_clientes)
            total_buzones = len(df_buzones)
            google_count = len(df_buzones[df_buzones['proveedor'] == 'google']) if total_buzones > 0 else 0
            ms_count = len(df_buzones[df_buzones['proveedor'] == 'microsoft']) if total_buzones > 0 else 0

            col_met1.metric("Total Clientes (Cuentas Maestra)", total_clientes)
            col_met2.metric("Total Buzones Protegidos", total_buzones)
            col_met3.metric("Distribución Google", f"{round((google_count/total_buzones)*100, 1)}%" if total_buzones > 0 else "0%")

            # Gráfica de Dona (Solo si hay buzones)
            if not df_buzones.empty:
                conteo_proveedores = df_buzones['proveedor'].value_counts().reset_index()
                conteo_proveedores.columns = ['proveedor', 'Cantidad']
                
                fig_pie = px.pie(
                    conteo_proveedores, 
                    values='Cantidad', 
                    names='proveedor',
                    hole=0.5,
                    color='proveedor',
                    color_discrete_map={'google': '#4285F4', 'microsoft': '#00A4EF'},
                    title="Buzones por Proveedor"
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                fig_pie.update_layout(showlegend=False, height=350)
                st.plotly_chart(fig_pie, use_container_width=True)
            
            st.markdown("---")
            
            # --- 1. TABLA RESUMEN DE CLIENTES ---
            st.subheader("👥 Base de Datos de Clientes")
            
            # Formateamos las fechas (Supabase devuelve timezone +00:00, hay que parsear con utc=True)
            if 'fecha_vencimiento' in df_clientes.columns:
                df_clientes['Vence_el'] = (
                    pd.to_datetime(df_clientes['fecha_vencimiento'], utc=True, errors='coerce')
                    .dt.strftime('%d/%m/%Y')
                    .fillna('Sin Vencimiento')
                )
            else:
                df_clientes['Vence_el'] = 'Sin Vencimiento'

            # Preparamos las columnas que queremos ver
            df_vista = df_clientes[['nombre', 'email_principal', 'plan_tipo', 'limite_buzones', 'buzones_vinculados', 'estado_suscripcion', 'Vence_el']].copy()
            df_vista.columns = ['Nombre', 'Correo Maestro', 'Plan', 'Límite', 'Buzones Asociados', 'Estado', 'Vencimiento']
            
            # Pintamos de rojo los suspendidos en la tabla (opcional con pandas styling, o simplemente mostramos)
            st.dataframe(df_vista, use_container_width=True, hide_index=True)

            st.markdown("---")
            
            # --- 2. HERRAMIENTA DE GESTIÓN DE SUSCRIPCIONES Y CUENTAS ---
            st.subheader("⚙️ Gestión Detallada de Cliente")
            
            # Buscador de cliente
            cliente_seleccionado = st.selectbox("Seleccionar Cliente a inspeccionar:", df_clientes['email_principal'].tolist())
            datos_cliente = df_clientes[df_clientes['email_principal'] == cliente_seleccionado].iloc[0]
            
            with st.container(border=True):
                st.markdown(f"### Cliente: `{cliente_seleccionado}`")
                
                col_izq, col_der = st.columns([1, 1.5])
                
                with col_izq:
                    st.markdown("#### 📧 Buzones Asociados")
                    # Filtramos los buzones de este cliente
                    buzones_del_cliente = df_buzones[df_buzones['cliente_id'] == datos_cliente['id']] if not df_buzones.empty else pd.DataFrame()
                    
                    if not buzones_del_cliente.empty:
                        # Mostramos un resumen del uso del plan
                        usados = len(buzones_del_cliente[buzones_del_cliente['estado_proteccion'] == 'activo'])
                        limite = datos_cliente['limite_buzones']
                        st.caption(f"Uso: {usados} de {limite} buzones activos")

                        for _, row in buzones_del_cliente.iterrows():
                            with st.container(border=True):
                                col_mail, col_btn = st.columns([3, 1])
                                icono = "🌐" if row['proveedor'] == "google" else "🟦"
                                estado = row['estado_proteccion']
                                
                                col_mail.markdown(f"{icono} **{row['email_cuenta']}**\n\n`Status: {estado}`")
                                
                                # Si el buzón está desactivado pero hay cupo en el plan, permitimos activarlo
                                if estado != "activo":
                                    if col_btn.button("Activar", key=f"btn_{row['id']}"):
                                        if usados < limite:
                                            supabase.table("buzones_monitoreados").update({
                                                "estado_proteccion": "activo"
                                            }).eq("id", row['id']).execute()
                                            st.success(f"Activado: {row['email_cuenta']}")
                                            st.rerun()
                                        else:
                                            st.error("Límite de plan alcanzado")
                                else:
                                    if col_btn.button("Pausar", key=f"btn_{row['id']}"):
                                        supabase.table("buzones_monitoreados").update({
                                            "estado_proteccion": "inactivo"
                                        }).eq("id", row['id']).execute()
                                        st.rerun()
                    else:
                        st.warning("Este cliente no ha registrado buzones.")

                with col_der:
                    st.markdown("#### 🛠️ Actualizar Membresía")
                    
                    col_p1, col_p2 = st.columns(2)
                    
                    # 1. Estado
                    nuevo_estado = col_p1.selectbox(
                        "Estado del Servicio:", 
                        ["activo", "suspendido"], 
                        index=0 if datos_cliente['estado_suscripcion'] == "activo" else 1
                    )
                    
                    # 2. Plan
                    planes = ["Individual", "Profesional", "Enterprise"]
                    plan_actual = datos_cliente['plan_tipo'] if datos_cliente['plan_tipo'] in planes else "Individual"
                    nuevo_plan = col_p2.selectbox("Nivel de Plan:", planes, index=planes.index(plan_actual))
                    
                    # 3. Fechas
                    fecha_pago = col_p1.date_input("Fecha de renovación:", value=datetime.now())
                    meses = col_p2.number_input("Meses a extender:", min_value=1, max_value=24, value=1)

                    if st.button("Guardar Cambios del Cliente", type="primary", use_container_width=True):
                        fecha_fin = fecha_pago + timedelta(days=30 * meses)
                        limites_dict = {"Individual": 1, "Profesional": 5, "Enterprise": 20}
                        
                        if nuevo_estado == "suspendido": 
                        # --- NUEVO: 2. Actualizamos en cascada los buzones asociados ---
                        # Traducimos el estado comercial al estado técnico del buzón
                            estado_buzon = "inactivo" 
                        else:
                            estado_buzon = "activo" if nuevo_estado == "activo" else "Desvinculado"
                        
                        
                        # 1. Actualizamos la tabla maestra (clientes)
                        supabase.table("clientes").update({
                            "estado_suscripcion": nuevo_estado,
                            "plan_tipo": nuevo_plan,
                            "limite_buzones": limites_dict[nuevo_plan],
                            "fecha_vencimiento": fecha_fin.isoformat()
                        }).eq("email_principal", cliente_seleccionado).execute()
                        
                        
                        
                        # Actualizamos todos los buzones que pertenezcan al ID de este cliente
                        supabase.table("buzones_monitoreados").update({
                            "estado_proteccion": estado_buzon
                        }).eq("cliente_id", datos_cliente['id']).execute()
                        # ---------------------------------------------------------------
                        
                        if nuevo_estado == "activo":
                            st.success(f"✅ Cuenta y buzones ACTIVADOS. Plan {nuevo_plan}. Vence: {fecha_fin.strftime('%d/%m/%Y')}")
                        else:
                            st.error(f"❌ Cuenta SUSPENDIDA. Todos los buzones asociados han sido desactivados.")
                            
                        st.rerun()

        else:
            st.info("Aún no tienes clientes registrados en la base de datos maestra.")

    except Exception as e:
        st.error(f"Error cargando la base de datos maestra: {e}")

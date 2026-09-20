import pyotp
import os

def render_login(st, ADMIN_PASSWORD):
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; font-size: 4em;'>👑</h1>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>Acceso Maestro</h2>", unsafe_allow_html=True)

        with st.container(border=True):
            if st.session_state.admin_bloqueado:
                st.error("🔒 Demasiados intentos fallidos. Cerrá y volvé a abrir el navegador.")
                return

            if st.session_state.admin_intentos > 0:
                restantes = 5 - st.session_state.admin_intentos
                st.warning(f"⚠️ Intentos restantes: {restantes}")

            if not ADMIN_PASSWORD:
                st.error("❌ BACKOFFICE_PASSWORD no está configurada en el .env")
                return

            pwd = st.text_input("Contraseña de Administrador", type="password",
                                placeholder="Ingresa la clave maestra...")
            codigo_totp = st.text_input("Código Google Authenticator (6 dígitos)",
                                        placeholder="123456", max_chars=6)

            if st.button("Desbloquear Sistema", use_container_width=True, type="primary"):
                if pwd != ADMIN_PASSWORD:
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

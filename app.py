import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Vambe Motors · Dashboard", layout="wide")

# 1. Abrir el archivo: busca el .db que está en la misma carpeta que app.py
CARPETA = Path(__file__).parent


@st.cache_resource
def ubicar_base():
    # Si está el .db, se usa directo (por ejemplo, en tu computador)
    dbs = sorted(CARPETA.glob("*.db"))
    if dbs:
        return dbs[0]
    # Si solo está comprimido (.db.gz), se descomprime una vez, sin modificar el original
    comprimido = sorted(CARPETA.glob("*.db.gz"))[0]
    destino = Path(tempfile.gettempdir()) / comprimido.name.removesuffix(".gz")
    if not destino.exists():
        with gzip.open(comprimido, "rb") as origen, open(destino, "wb") as salida:
            shutil.copyfileobj(origen, salida)
    return destino


DB_PATH = ubicar_base()


# 2. Función para leer datos con SQL
#    params: valores que se pasan por separado a la consulta (los "?")
#    st.cache_data evita repetir una consulta que ya se hizo
@st.cache_data
def leer(query, params=()):
    # Solo lectura: no modifica el archivo y funciona en servidores sin permisos de escritura
    with sqlite3.connect(DB_PATH.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        return pd.read_sql(query, conn, params=params)


st.title("Vambe Motors")

# 3. Pestañas del dashboard
tab_kpis, tab_hallazgos, tab_explorar = st.tabs(
    ["KPIs comerciales", "Hallazgos", "Explorar datos"])


# ============================================================
# Pestaña 1: KPIs comerciales
# ============================================================
with tab_kpis:
    # Resumen general
    resumen = leer("""
        SELECT COUNT(*) AS leads,
               SUM(et.nombre = 'Ganado')  AS ganados,
               SUM(et.nombre = 'Perdido') AS perdidos,
               SUM(et.es_terminal = 0)    AS en_proceso,
               MIN(c.creado_en)           AS desde,
               MAX(c.creado_en)           AS hasta
        FROM contactos c
        JOIN etapas_embudo et ON et.id = c.etapa_actual_id
    """).iloc[0]

    st.caption(f"Leads recibidos entre el {resumen['desde'][:10]} y el {resumen['hasta'][:10]}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leads", f"{resumen['leads']:,}".replace(",", "."))
    c2.metric("Ganados", f"{resumen['ganados'] / resumen['leads']:.1%}",
              f"{resumen['ganados']:,} ventas".replace(",", "."), delta_color="off")
    c3.metric("Perdidos", f"{resumen['perdidos'] / resumen['leads']:.1%}")
    c4.metric("En proceso", f"{resumen['en_proceso'] / resumen['leads']:.1%}")
    st.divider()

    # 0. Canal de origen de los leads
    st.subheader("¿De dónde vienen los mejores leads?")
    canales = leer("""
        WITH orden AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY contacto_id
                                      ORDER BY enviado_en, id) AS n
            FROM mensajes
        ),
        primer_mensaje AS (
            SELECT m.contacto_id, m.direccion, m.payload
            FROM mensajes m
            JOIN orden o ON o.id = m.id
            WHERE o.n = 1
        ),
        origen AS (
            SELECT p.contacto_id,
                   CASE
                       WHEN json_extract(p.payload, '$.referral.source_type') = 'ad'
                           THEN 'Anuncio Meta'
                       WHEN p.direccion = 'saliente'
                           THEN 'Campaña WhatsApp'
                       ELSE 'Directo (sin atribución)'
                   END AS origen,
                   COALESCE(a.campaign_name,
                            json_extract(p.payload, '$.template.name'),
                            'directo') AS detalle
            FROM primer_mensaje p
            LEFT JOIN catalogo_anuncios a
                   ON a.ad_id = json_extract(p.payload, '$.referral.source_id')
        )
        SELECT o.origen,
               o.detalle,
               COUNT(*) AS leads,
               SUM(et.nombre = 'Ganado')  AS ganados,
               SUM(et.nombre = 'Perdido') AS perdidos
        FROM origen o
        JOIN contactos c      ON c.id = o.contacto_id
        JOIN etapas_embudo et ON et.id = c.etapa_actual_id
        GROUP BY o.origen, o.detalle
    """)

    def agregar_tasas(df):
        df["% conversión"] = (100 * df["ganados"] / df["leads"]).round(1)
        df["% cierre"] = (100 * df["ganados"] / (df["ganados"] + df["perdidos"])).round(1)
        return df

    por_canal = agregar_tasas(
        canales.groupby("origen")[["leads", "ganados", "perdidos"]].sum().reset_index())

    col1, col2 = st.columns(2)
    with col1:
        st.caption("Leads por origen")
        st.bar_chart(por_canal.set_index("origen")["leads"], horizontal=True)
    with col2:
        st.caption("% cierre por origen (ganados / leads cerrados)")
        st.bar_chart(por_canal.set_index("origen")["% cierre"], horizontal=True)

    st.dataframe(por_canal, hide_index=True)

    with st.expander("Ver detalle por anuncio y por campaña"):
        detalle = agregar_tasas(canales.copy()).sort_values(["origen", "% cierre"],
                                                            ascending=[True, False])
        st.dataframe(detalle, hide_index=True)

    st.caption("El origen se identifica con el primer mensaje de cada lead: si el cliente "
               "escribió desde un anuncio (campo referral), si la automotora le escribió "
               "primero con una plantilla, o si escribió sin un origen registrado. "
               "Los anuncios de Google no registran origen en WhatsApp, por lo que esos "
               "leads quedan como 'Directo'.")
    st.divider()

    # 1. Resultado de los leads por embudo
    st.subheader("Resultado de los leads por embudo")
    resultado = leer("""
        SELECT e.nombre AS embudo,
               COUNT(*) AS leads,
               SUM(et.nombre = 'Ganado')  AS ganados,
               SUM(et.nombre = 'Perdido') AS perdidos,
               SUM(et.es_terminal = 0)    AS en_proceso
        FROM contactos c
        JOIN etapas_embudo et ON et.id = c.etapa_actual_id
        JOIN embudos e        ON e.id  = c.embudo_id
        GROUP BY e.nombre
    """)
    resultado["% ganados"] = (100 * resultado["ganados"] / resultado["leads"]).round(1)
    resultado["% perdidos"] = (100 * resultado["perdidos"] / resultado["leads"]).round(1)
    resultado["% en proceso"] = (100 * resultado["en_proceso"] / resultado["leads"]).round(1)

    cols = st.columns(len(resultado))
    for col, (_, fila) in zip(cols, resultado.iterrows()):
        col.metric(f"{fila['embudo']}: ganados", f"{fila['% ganados']}%")
        col.metric(f"{fila['embudo']}: perdidos", f"{fila['% perdidos']}%")

    st.bar_chart(resultado.set_index("embudo")[["% ganados", "% perdidos", "% en proceso"]],
                 horizontal=True)
    st.dataframe(resultado, hide_index=True)

    # 2. Desempeño por vendedor
    st.subheader("Desempeño por vendedor")
    vendedores = leer("""
        SELECT m.nombre AS vendedor,
               COUNT(*) AS leads,
               SUM(et.nombre = 'Ganado')  AS ganados,
               SUM(et.nombre = 'Perdido') AS perdidos
        FROM contactos c
        JOIN etapas_embudo et   ON et.id = c.etapa_actual_id
        JOIN miembros_equipo m  ON m.id  = c.vendedor_id
        GROUP BY m.nombre
    """)
    vendedores["% conversión"] = (100 * vendedores["ganados"] / vendedores["leads"]).round(1)
    vendedores["% cierre"] = (100 * vendedores["ganados"]
                              / (vendedores["ganados"] + vendedores["perdidos"])).round(1)
    vendedores = vendedores.sort_values("% conversión", ascending=False)

    st.bar_chart(vendedores.set_index("vendedor")["% conversión"], horizontal=True)
    st.dataframe(vendedores, hide_index=True)
    st.caption("% conversión = ganados / leads asignados. "
               "% cierre = ganados / (ganados + perdidos), es decir, solo sobre leads ya cerrados.")

    # 3. Embudo de conversión: % de leads que llegó al menos a cada etapa
    st.subheader("Embudo de conversión")
    embudo_conv = leer("""
        WITH visitadas AS (
            SELECT entidad_id AS contacto_id, CAST(valor_anterior AS INTEGER) AS etapa_id
            FROM registros_cambios
            WHERE entidad_tipo = 'contacto' AND campo = 'etapa_id'
            UNION
            SELECT entidad_id, CAST(valor_nuevo AS INTEGER)
            FROM registros_cambios
            WHERE entidad_tipo = 'contacto' AND campo = 'etapa_id'
            UNION
            SELECT id, etapa_actual_id FROM contactos
        ),
        maximo AS (
            SELECT v.contacto_id, MAX(et.orden) AS orden_max
            FROM visitadas v
            JOIN etapas_embudo et ON et.id = v.etapa_id
            WHERE et.nombre <> 'Perdido'
            GROUP BY v.contacto_id
        )
        SELECT e.nombre AS embudo,
               et.orden,
               et.nombre AS etapa,
               COUNT(m.contacto_id) AS leads
        FROM etapas_embudo et
        JOIN embudos e   ON e.id = et.embudo_id
        JOIN contactos c ON c.embudo_id = et.embudo_id
        LEFT JOIN maximo m ON m.contacto_id = c.id AND m.orden_max >= et.orden
        WHERE et.nombre <> 'Perdido'
        GROUP BY e.nombre, et.orden, et.nombre
        ORDER BY e.nombre, et.orden
    """)

    embudo_sel = st.selectbox("Embudo", embudo_conv["embudo"].unique())
    datos_embudo = embudo_conv[embudo_conv["embudo"] == embudo_sel].copy()
    datos_embudo["% de los leads"] = (100 * datos_embudo["leads"]
                                      / datos_embudo["leads"].iloc[0]).round(1)
    datos_embudo["etapa"] = datos_embudo["orden"].astype(str) + ". " + datos_embudo["etapa"]

    st.bar_chart(datos_embudo.set_index("etapa")["% de los leads"], horizontal=True)
    st.dataframe(datos_embudo[["etapa", "leads", "% de los leads"]], hide_index=True)
    # 4. Tiempo de respuesta
    st.subheader("Tiempo de respuesta")
    respuestas = leer("""
        WITH ordenados AS (
            SELECT contacto_id, direccion, remitente_tipo, enviado_en,
                   LAG(direccion)  OVER (PARTITION BY contacto_id ORDER BY enviado_en, id)
                       AS direccion_anterior,
                   LAG(enviado_en) OVER (PARTITION BY contacto_id ORDER BY enviado_en, id)
                       AS hora_anterior
            FROM mensajes
        )
        SELECT remitente_tipo AS responde,
               (julianday(enviado_en) - julianday(hora_anterior)) * 1440 AS minutos
        FROM ordenados
        WHERE direccion = 'saliente' AND direccion_anterior = 'entrante'
    """)

    def formato_tiempo(minutos):
        if minutos < 60:
            return f"{minutos:.0f} min"
        if minutos < 1440:
            return f"{minutos / 60:.1f} h"
        return f"{minutos / 1440:.1f} días"

    c1, c2, c3 = st.columns(3)
    c1.metric("Tiempo de respuesta (mediana)", formato_tiempo(respuestas["minutos"].median()))
    c2.metric("Respuestas en menos de 5 minutos", f"{(respuestas['minutos'] < 5).mean():.1%}")
    c3.metric("Respuestas que tardan más de 1 día", f"{(respuestas['minutos'] > 1440).mean():.1%}")

    # Comparación entre asistentes IA y vendedores
    por_tipo = respuestas.groupby("responde")["minutos"].agg(
        respuestas="count",
        mediana="median",
        p90=lambda m: m.quantile(0.9),
        menos_5_min=lambda m: (m < 5).mean() * 100,
    ).reset_index()
    por_tipo["mediana"] = por_tipo["mediana"].apply(formato_tiempo)
    por_tipo["p90"] = por_tipo["p90"].apply(formato_tiempo)
    por_tipo["menos_5_min"] = por_tipo["menos_5_min"].round(1)
    st.dataframe(por_tipo.rename(columns={"menos_5_min": "% en menos de 5 min"}),
                 hide_index=True)

    # Distribución de los tiempos de respuesta
    tramos = pd.cut(respuestas["minutos"],
                    bins=[-1, 5, 60, 1440, float("inf")],
                    labels=["1. Menos de 5 min", "2. Entre 5 y 60 min",
                            "3. Entre 1 y 24 h", "4. Más de 1 día"])
    st.bar_chart(tramos.value_counts(normalize=True).sort_index() * 100, horizontal=True)
    st.caption("Se mide el tiempo entre el último mensaje del cliente y la primera "
               "respuesta de la automotora (asistente IA o vendedor).")

    # 5. Abandono: en qué etapa se pierden los leads
    st.subheader("¿En qué etapa se pierden los leads?")
    perdidas = leer("""
        WITH cambios_a_perdido AS (
            SELECT r.entidad_id AS contacto_id,
                   CAST(r.valor_anterior AS INTEGER) AS etapa_previa_id,
                   r.autor_tipo,
                   ROW_NUMBER() OVER (PARTITION BY r.entidad_id
                                      ORDER BY r.creado_en DESC) AS n
            FROM registros_cambios r
            JOIN etapas_embudo destino ON destino.id = CAST(r.valor_nuevo AS INTEGER)
            WHERE r.entidad_tipo = 'contacto'
              AND r.campo = 'etapa_id'
              AND destino.nombre = 'Perdido'
        )
        SELECT e.nombre    AS embudo,
               previa.orden,
               previa.nombre AS etapa,
               COUNT(*)    AS perdidos,
               SUM(p.autor_tipo = 'asistente') AS marcados_por_ia
        FROM cambios_a_perdido p
        JOIN contactos c          ON c.id = p.contacto_id
        JOIN etapas_embudo actual ON actual.id = c.etapa_actual_id
        JOIN etapas_embudo previa ON previa.id = p.etapa_previa_id
        JOIN embudos e            ON e.id = c.embudo_id
        WHERE p.n = 1 AND actual.nombre = 'Perdido'
        GROUP BY e.nombre, previa.orden, previa.nombre
        ORDER BY e.nombre, previa.orden
    """)

    embudo_ab = st.selectbox("Embudo", perdidas["embudo"].unique(), key="embudo_abandono")
    ab = perdidas[perdidas["embudo"] == embudo_ab].copy()

    # Tasa de pérdida: perdidos desde una etapa / leads que llegaron a esa etapa
    llegaron = embudo_conv[embudo_conv["embudo"] == embudo_ab][["orden", "leads"]]
    ab = ab.merge(llegaron.rename(columns={"leads": "llegaron"}), on="orden", how="left")
    ab["% de los perdidos"] = (100 * ab["perdidos"] / ab["perdidos"].sum()).round(1)
    ab["tasa de pérdida"] = (100 * ab["perdidos"] / ab["llegaron"]).round(1)
    ab["etapa"] = ab["orden"].astype(str) + ". " + ab["etapa"]

    col1, col2 = st.columns(2)
    with col1:
        st.caption("Del total de perdidos, ¿en qué etapa estaban?")
        st.bar_chart(ab.set_index("etapa")["% de los perdidos"], horizontal=True)
    with col2:
        st.caption("De los que llegaron a cada etapa, ¿qué % se perdió ahí?")
        st.bar_chart(ab.set_index("etapa")["tasa de pérdida"], horizontal=True)

    st.dataframe(ab[["etapa", "llegaron", "perdidos", "% de los perdidos",
                     "tasa de pérdida", "marcados_por_ia"]], hide_index=True)


# ============================================================
# Pestaña 2: Hallazgos de calidad de datos
# ============================================================
with tab_hallazgos:
    # Hallazgo: integración con HubSpot
    st.header("Integración con HubSpot")

    varios_ids = leer("""
        SELECT contacto_id,
               COUNT(*) AS sincronizaciones,
               COUNT(DISTINCT external_id) AS ids_hubspot
        FROM integracion_crm
        GROUP BY contacto_id
        HAVING COUNT(DISTINCT external_id) > 1
        ORDER BY ids_hubspot DESC
    """)

    id_compartido = leer("""
        SELECT external_id, COUNT(DISTINCT contacto_id) AS contactos
        FROM integracion_crm
        GROUP BY external_id
        HAVING COUNT(DISTINCT contacto_id) > 1
        ORDER BY contactos DESC
    """)

    total_sync = leer("SELECT COUNT(DISTINCT contacto_id) AS n FROM integracion_crm")["n"][0]

    c1, c2 = st.columns(2)
    c1.metric("Contactos con varios IDs en HubSpot", len(varios_ids),
              f"{len(varios_ids) / total_sync:.1%} de los sincronizados", delta_color="off")
    c2.metric("IDs de HubSpot compartidos", len(id_compartido),
              help="Un mismo ID de HubSpot asignado a contactos distintos de Vambe")

    with st.expander("Ver contactos con varios IDs en HubSpot"):
        st.dataframe(varios_ids, hide_index=True)

    with st.expander("Ver IDs de HubSpot compartidos entre contactos"):
        st.dataframe(id_compartido, hide_index=True)

    # Historial de un contacto, para entender qué pasa en cada sincronización
    st.subheader("Historial de sincronización de un contacto")
    contacto = st.number_input("ID del contacto", min_value=1, value=1)
    historial = leer("""
        SELECT sincronizado_en,
               external_id,
               estado_sync,
               json_extract(payload, '$.hs_object_id')   AS hs_object_id,
               json_extract(payload, '$.lifecyclestage') AS lifecyclestage,
               json_extract(payload, '$.hs_lead_status') AS hs_lead_status
        FROM integracion_crm
        WHERE contacto_id = ?
        ORDER BY sincronizado_en
    """, (int(contacto),))
    st.dataframe(historial, hide_index=True)

    st.divider()

    # Hallazgo: citas
    st.header("Citas")

    resumen_citas = leer("""
        SELECT COUNT(*) AS total_citas,
               SUM(CAST(strftime('%H', agendada_para) AS INTEGER) NOT BETWEEN 9 AND 19)
                   AS fuera_de_horario,
               SUM(CAST(strftime('%H', agendada_para) AS INTEGER) NOT BETWEEN 9 AND 19
                   AND estado = 'completada') AS completadas_fuera_de_horario,
               SUM(strftime('%H:%M', agendada_para) = strftime('%H:%M', creada_en))
                   AS misma_hora_que_creacion
        FROM citas
    """).iloc[0]

    total = resumen_citas["total_citas"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Citas fuera de horario (antes de 9:00 o desde 20:00)",
              f"{resumen_citas['fuera_de_horario'] / total:.1%}")
    c2.metric("Citas completadas fuera de horario",
              int(resumen_citas["completadas_fuera_de_horario"]))
    c3.metric("Citas con la misma hora en que se agendaron",
              f"{resumen_citas['misma_hora_que_creacion'] / total:.1%}")

    # Gráfico: cantidad de citas según la hora agendada
    st.subheader("Citas según la hora agendada")
    por_hora = leer("""
        SELECT CAST(strftime('%H', agendada_para) AS INTEGER) AS hora,
               COUNT(*) AS citas
        FROM citas
        GROUP BY hora
        ORDER BY hora
    """)
    st.bar_chart(por_hora.set_index("hora")["citas"])
    st.caption("Una automotora atiende aproximadamente entre las 9:00 y las 20:00.")


# ============================================================
# Pestaña 3: Explorar datos
# ============================================================
with tab_explorar:
    # Explorar datos

    tablas = leer("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")["name"]

    tabla = st.selectbox("Tabla", tablas)

    # Controles de búsqueda
    columnas = leer(f"PRAGMA table_info({tabla})")["name"].tolist()
    c1, c2, c3 = st.columns([1, 2, 1])
    columna = c1.selectbox("Buscar en la columna", ["(todas)"] + columnas)
    valor = c2.text_input("Valor a buscar", placeholder="Ej.: Santiago, no_show, 1523")
    exacto = c3.checkbox("Coincidencia exacta")

    # Armar el filtro SQL según lo que se eligió
    where, params = "", []
    if valor:
        cols = columnas if columna == "(todas)" else [columna]
        if exacto:
            condiciones = [f'CAST("{c}" AS TEXT) = ?' for c in cols]
            params = [valor] * len(cols)
        else:
            condiciones = [f'CAST("{c}" AS TEXT) LIKE ?' for c in cols]
            params = [f"%{valor}%"] * len(cols)
        where = "WHERE " + " OR ".join(condiciones)

    # Contar y paginar el resultado filtrado
    total = leer(f"SELECT COUNT(*) AS n FROM {tabla} {where}", tuple(params))["n"][0]
    POR_PAGINA = 1000
    paginas = max(1, -(-total // POR_PAGINA))

    col1, col2 = st.columns(2)
    col1.metric("Filas", total)
    pagina = col2.number_input(f"Página (de {paginas})", 1, paginas, 1,
                               key=f"pag_{tabla}_{columna}_{valor}_{exacto}")

    offset = (pagina - 1) * POR_PAGINA
    datos = leer(f"SELECT * FROM {tabla} {where} LIMIT {POR_PAGINA} OFFSET {offset}",
                 tuple(params))
    st.dataframe(datos, hide_index=True)
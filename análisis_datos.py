import sqlite3
import pandas as pd

conn = sqlite3.connect("vambe_concesionaria.db")

tablas = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", conn)
for t in tablas["name"]:
    df = pd.read_sql(f"SELECT * FROM {t}", conn)
    print(f"\n=== {t} ({len(df)} filas) ===")
    print(df.dtypes)
    print(df.head(3))

print(pd.read_sql("""
    SELECT telefono, COUNT(*) AS veces
    FROM contactos
    GROUP BY telefono
    HAVING COUNT(*) > 1
    ORDER BY veces DESC
""", conn))

resultado = pd.read_sql("""
    SELECT ca.nombre,
           COUNT(*) AS envios,
           SUM(cc.enviado_en < c.creado_en) AS antes_de_existir,
           SUM(julianday(cc.enviado_en) - julianday(c.creado_en) < 1) AS primer_dia,
           SUM(julianday(cc.enviado_en) - julianday(c.creado_en) < 7) AS primera_semana
    FROM campana_contactos cc
    JOIN contactos c  ON c.id  = cc.contacto_id
    JOIN campanas  ca ON ca.id = cc.campana_id
    GROUP BY ca.nombre
""", conn)

print(resultado.to_string())

print(pd.read_sql("""
    SELECT e.nombre AS embudo, m.valor AS tipo_vehiculo, COUNT(*) AS contactos
    FROM metadata_contacto m
    JOIN contactos c ON c.id = m.contacto_id
    JOIN embudos e   ON e.id = c.embudo_id
    WHERE m.clave = 'tipo_vehiculo'
    GROUP BY e.nombre, m.valor
""", conn).to_string())

print(pd.read_sql("""
    SELECT COUNT(*) AS citas,
           SUM(m.valor = ci.modelo_interes) AS modelo_coincide
    FROM citas ci
    JOIN metadata_contacto m
      ON m.contacto_id = ci.contacto_id AND m.clave = 'modelo_interes'
""", conn).to_string())

print(pd.read_sql("""
    SELECT contenido, COUNT(*) AS veces
    FROM notas_chat
    GROUP BY contenido
    ORDER BY veces DESC
""", conn).to_string())

print(pd.read_sql("""
    SELECT m.valor AS metadata_dice, COUNT(*) AS notas
    FROM notas_chat n
    JOIN metadata_contacto m
      ON m.contacto_id = n.contacto_id AND m.clave = 'tiene_vehiculo_parte_pago'
    WHERE n.contenido LIKE 'Quiere entregar su auto%'
    GROUP BY m.valor
""", conn).to_string())
print(pd.read_sql("""
    SELECT valor, COUNT(*) AS clientes,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS porcentaje
    FROM metadata_contacto
    WHERE clave = 'tiene_vehiculo_parte_pago'
    GROUP BY valor
""", conn).to_string())

print(pd.read_sql("""
    SELECT et.nombre AS etapa_actual, COUNT(*) AS notas
    FROM notas_chat n
    JOIN contactos c      ON c.id = n.contacto_id
    JOIN etapas_embudo et ON et.id = c.etapa_actual_id
    WHERE n.contenido LIKE 'Compró%'
    GROUP BY et.nombre
    ORDER BY notas DESC
""", conn).to_string())

print(pd.read_sql("""
    SELECT MIN(creado_en) AS primer_lead, MAX(creado_en) AS ultimo_lead
    FROM contactos
""", conn).to_string())

print(pd.read_sql("""
    SELECT ca.nombre, ca.programada_para,
           MIN(cc.enviado_en) AS primer_envio,
           MAX(cc.enviado_en) AS ultimo_envio
    FROM campanas ca
    JOIN campana_contactos cc ON cc.campana_id = ca.id
    GROUP BY ca.nombre
""", conn).to_string())

print(pd.read_sql("""
    SELECT contacto_id, direccion, payload
    FROM mensajes
    WHERE payload IS NOT NULL
    LIMIT 5
""", conn).to_string())

print(pd.read_sql("""
    SELECT COUNT(DISTINCT contacto_id) AS contactos_con_campana
    FROM campana_contactos
""", conn).to_string())

print(pd.read_sql("""
    WITH primero AS (
        SELECT contacto_id, direccion, payload,
               ROW_NUMBER() OVER (PARTITION BY contacto_id
                                  ORDER BY enviado_en, id) AS n
        FROM mensajes
    )
    SELECT direccion,
           json_extract(payload, '$.referral.source_type') AS tipo_referral,
           json_extract(payload, '$.referral.source_id')   AS anuncio,
           json_extract(payload, '$.template.name')        AS plantilla,
           payload IS NULL                                 AS sin_payload,
           COUNT(*) AS contactos
    FROM primero
    WHERE n = 1
    GROUP BY 1, 2, 3, 4, 5
    ORDER BY contactos DESC
""", conn).to_string())

print(pd.read_sql("""
    WITH orden AS (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY contacto_id
                                      ORDER BY enviado_en, id) AS n
        FROM mensajes
    ),
    origen AS (
        SELECT m.contacto_id,
               CASE
                   WHEN json_extract(m.payload, '$.referral.source_type') = 'ad'
                       THEN 'Meta'
                   WHEN m.direccion = 'saliente' THEN 'Campaña'
                   ELSE 'Directo'
               END AS canal
        FROM mensajes m JOIN orden o ON o.id = m.id
        WHERE o.n = 1
    )
    SELECT mi.nombre AS vendedor, o.canal,
           COUNT(*) AS leads,
           ROUND(100.0 * SUM(et.nombre = 'Ganado')
                 / SUM(et.nombre IN ('Ganado', 'Perdido')), 1) AS pct_cierre
    FROM origen o
    JOIN contactos c        ON c.id = o.contacto_id
    JOIN etapas_embudo et   ON et.id = c.etapa_actual_id
    JOIN miembros_equipo mi ON mi.id = c.vendedor_id
    GROUP BY mi.nombre, o.canal
    ORDER BY o.canal, pct_cierre DESC
""", conn).to_string())

print(pd.read_sql("""
    SELECT r.autor_tipo, COUNT(*) AS leads
    FROM registros_cambios r
    JOIN etapas_embudo antes   ON antes.id   = CAST(r.valor_anterior AS INTEGER)
    JOIN etapas_embudo despues ON despues.id = CAST(r.valor_nuevo AS INTEGER)
    WHERE r.entidad_tipo = 'contacto'
      AND r.campo = 'etapa_id'
      AND antes.nombre = 'Nuevo Lead'
      AND despues.nombre = 'Perdido'
    GROUP BY r.autor_tipo
""", conn).to_string())
# Seguridad y manejo de datos sensibles

Como se protegen los secretos de la infraestructura y los datos
personales (PII) a lo largo del Lakehouse. Los datos del proyecto son
sinteticos, pero se tratan con las mismas reglas que se aplicarian a
datos reales de clientes: el objetivo es que el diseno sea defendible
como si lo fueran.

Ultima revision: 2026-10-01.

## Principios

1. **Nada sensible en el repositorio.** Ni secretos ni datos. Todo lo
   generado vive en `data/` y `models/`, fuera de git (`.gitignore`) y
   fuera de la imagen de Docker (`.dockerignore`). El CI lo verifica en
   cada PR.
2. **PII en claro solo donde es inevitable.** Existe en la zona de
   aterrizaje y en Bronze (el registro fiel de lo que llego); desde
   Silver en adelante solo existen versiones protegidas.
3. **Secretos por maquina, nunca compartidos.** Cada instalacion genera
   los suyos; no existe una contrasena "del proyecto".
4. **Minima exposicion de red.** Los servicios solo escuchan en la
   maquina local.

## Datos personales sinteticos

El generador crea, por cliente: nombre y apellidos, sexo, estado de
nacimiento, CURP, RFC (excepto estudiantes), telefono, correo y
domicilio. CURP y RFC se calculan con los algoritmos oficiales a partir
de los datos del propio cliente, asi que son coherentes con su nombre y
fecha de nacimiento. Para que ningun dato coincida con el de una persona
real:

- Los correos usan `example.com`, `example.org` y `example.net`,
  dominios reservados que no pertenecen a nadie (RFC 2606).
- Los telefonos usan ladas reales, pero el numero local empieza con 0;
  en Mexico ningun numero asignado empieza asi.

## Clasificacion de datos por capa

| Zona | PII | Acceso esperado en un banco |
|---|---|---|
| Fuentes / sesiones (`data/sesiones/`, MinIO) | En claro | Restringido: procesos de ingesta y auditoria |
| Bronze | En claro | Restringido: ingenieria de datos, auditoria |
| Cuarentena de Silver | Protegida, igual que Silver | Ingenieria de datos (diagnostico) |
| Silver | Hash + enmascarado + generalizada | Analistas, ciencia de datos |
| Gold, feature store, modelo | Sin PII | Negocio, dashboards, asistente |
| Respaldos (`demo.sh respaldar`) | En claro (hereda la de la capa mas sensible que contiene) | Restringido, igual que las fuentes: no se versionan ni se comparten |

Un respaldo copia `data/sesiones/`, `data/minio/` y `data/calidad/`, es decir la
zona de fuentes y la landing zone, que van en claro. No incluye `.env`, asi que
tampoco la sal (`PII_HASH_SALT`), que se guarda aparte.

Como se protege cada campo en Silver:

| Campo | Tratamiento | Ejemplo |
|---|---|---|
| CURP, RFC | Hash con sal (para unir y deduplicar) + enmascarado (para personas) | `GOMA**********09` |
| Telefono | Hash + enmascarado | `******4821` |
| Correo | Hash + enmascarado | `a***@ejemplo.com` |
| Nombre y apellidos | Hash; no se conserva legible | |
| Domicilio | Generalizacion: se conservan codigo postal, municipio y estado; calle y numero se descartan | |

**Hash con sal (HMAC-SHA256).** La misma CURP produce siempre el mismo
hash, asi que se puede unir y contar clientes unicos sin ver el valor.
La sal es un secreto (`PII_HASH_SALT` en `.env`): la CURP tiene una
estructura predecible, y sin sal alguien podria calcular el hash de
todas las combinaciones plausibles y compararlos.

Implementacion: `config/politica_pii.yaml` declara el tratamiento de
cada campo y `src/silver/pii.py` lo aplica, con expresiones nativas de
Spark. `transform_silver.py` verifica antes de escribir que ningun campo
de la politica siga en claro, y si alguno lo esta, detiene el pipeline.
El CI repite esa verificacion sobre el Silver que produce el smoke test.

**Validacion antes de proteger.** Las reglas de calidad corren sobre los
valores reales, porque verificar el digito de una CURP exige verla
completa. Para CURP y RFC se valida formato, digito verificador y
coherencia con la fecha de nacimiento del propio cliente
(`src/common/identificadores.py`, comprobado contra los ejemplos
publicos de RENAPO y el SAT).

**Cuarentena.** Las filas que fallan una regla de calidad se guardan
con la PII ya protegida. Para diagnosticar una fila se usa su llave y
`_source_file` para rastrearla en Bronze, que es la zona autorizada a
tener el valor real.

## Secretos

`setup.sh` genera `.env` con permisos 600 (solo el dueno puede leerlo):

| Variable | Protege |
|---|---|
| `POSTGRES_PASSWORD` | Base de metadata de Airflow |
| `AIRFLOW_ADMIN_PASSWORD` | Usuario `admin` de la UI de Airflow |
| `AIRFLOW_FERNET_KEY` | Cifrado de conexiones y variables guardadas por Airflow |
| `AIRFLOW_SECRET_KEY` | Firma de cookies de sesion de la UI |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | Zona de aterrizaje |
| `PII_HASH_SALT` | Irreversibilidad de los hashes de PII |

- Se generan una sola vez y se conservan en corridas posteriores. Rotar
  `POSTGRES_PASSWORD` exige recrear la base (`docker compose down -v`),
  y rotar `PII_HASH_SALT` cambia todos los hashes de Silver.
- `docker-compose.yml` usa `${VAR:?}`: si falta un secreto, compose no
  arranca, en vez de caer a un valor por default inseguro.
- El DAG pasa los secretos al worker con `private_environment` de
  DockerOperator, que Airflow no muestra en la UI ni en los logs.
- `demo.sh` muestra las credenciales de acceso solo en la terminal
  local, al terminar.

## Red

Airflow (8080) y MinIO (9000, 9001) se publican en `127.0.0.1`: son
accesibles desde la maquina, no desde la red local.

## Controles automaticos en CI

| Control | Detecta |
|---|---|
| `detect-secrets` sobre los archivos versionados | Llaves de nube, contrasenas en texto, cadenas de alta entropia |
| Busqueda de archivos de datos versionados | `.csv`, `.parquet`, `.duckdb`, `.joblib`, `.env` |
| `shellcheck` | Errores en `setup.sh` y `demo.sh` |

## Auditoria del repositorio (2026-10-01)

Se revisaron las 34 entradas del historial en todas las ramas, incluida
`main`:

- Ningun secreto en el historial (llaves de nube, tokens, llaves
  privadas, credenciales de Google) ni archivos de credenciales
  (`credentials.json`, `token.json`), que siempre estuvieron en
  `.gitignore`.
- Ningun archivo de datos ni `.env` versionado en ningun momento.
- Corregido en esta revision: contrasenas fijas en `docker-compose.yml`
  (`airflow`, `admin`, `minioadmin`), puertos expuestos a toda la red,
  Airflow sin llave Fernet, y `.pytest_cache/` sin ignorar.

## Riesgos aceptados

| Riesgo | Por que se acepta | Mitigacion |
|---|---|---|
| Cuasi-identificadores en Silver: fecha de nacimiento, sexo y codigo postal juntos pueden reidentificar a una persona aun sin nombre ni CURP | La edad y la region son variables del modelo de riesgo y del analisis | Gold (KPIs y feature store) expone la edad, no la fecha exacta, y no incluye sexo ni codigo postal; para publicar datos fuera del equipo, generalizar a rangos de edad |
| El socket de Docker montado en Airflow equivale a control total del Docker del host | Es el mecanismo de DockerOperator para lanzar el worker | Uso local; puertos solo en 127.0.0.1 |
| El worker corre como root dentro del contenedor | Ver `docs/technical-debt.md` | Contenedor efimero, sin puertos |
| Imagenes `minio/minio:latest` y `minio/mc:latest` sin version fija | Pendiente de fijar | Registrado en `docs/technical-debt.md` |

## Repositorio en GitHub

- **Visibilidad: publico** (decision del 2026-10-01). El repositorio no
  contiene datos ni secretos, y en un repositorio publico GitHub ofrece
  gratis la proteccion de push, que en uno privado requiere GitHub
  Advanced Security. Publico da dos capas de control en vez de una.
- **Proteccion de push** (Settings > Code security > Secret Protection >
  Push protection): GitHub bloquea un push que contenga un secreto
  reconocido, antes de que llegue al historial.
- **Escaneo de secretos en CI.** `detect-secrets` en cada PR: cubre lo
  que la proteccion de push no reconoce (contrasenas genericas, cadenas
  de alta entropia) y no depende de la configuracion del repositorio.
- **Reglas de proteccion** en `main` y `develop` (Settings > Branches):
  PR obligatorio y CI en verde para mergear.

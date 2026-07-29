from machine import Pin, I2C
import network
import socket
import time
import math
import ujson


# =========================================================
# CONFIGURACIÓN
# =========================================================

NOMBRE_WIFI = "ESP32-Postura"
CLAVE_WIFI = "12345678"

PIN_SDA = 9
PIN_SCL = 10
PIN_MOTORES = 4
DIRECCION_MPU = 0x68

# Ángulo permitido antes de considerar mala postura
TOLERANCIA_INICIAL = 12.0

# Cambio mínimo para considerar que la persona se movio
UMBRAL_MOVIMIENTO = 3.0

# Tiempo sin movimiento para detectar sedentarismo.
# Para pruebas/prototipo: 60 segundos.
TIEMPO_SEDENTARISMO = 60

# Tiempo mínimo entre avisos de sedentarismo.
TIEMPO_ENTRE_AVISOS = 30

# Número máximo de eventos guardados en RAM.
MAXIMO_HISTORIAL = 40


# =========================================================
# CLASE PARA EL MPU6050
# =========================================================

class MPU6050:
    """
    Control básico del MPU6050.

    Lee el acelerómetro y calcula los ángulos frontal y lateral.
    """

    def __init__(self, i2c, direccion=DIRECCION_MPU):
        self.i2c = i2c
        self.direccion = direccion

        # Despertar el MPU6050.
        self.i2c.writeto_mem(
            self.direccion,
            0x6B,
            b"\x00"
        )

        time.sleep_ms(100)

    def leer_entero_16(self, registro):
        """
        Lee un número de 16 bits con signo.
        """
        datos = self.i2c.readfrom_mem(
            self.direccion,
            registro,
            2
        )

        valor = (datos[0] << 8) | datos[1]

        if valor > 32767:
            valor -= 65536

        return valor

    def leer_acelerometro(self):
        """
        Lee los tres ejes del acelerómetro.

        Retorna:
            ax, ay y az en unidades de gravedad.
        """
        ax = self.leer_entero_16(0x3B) / 16384
        ay = self.leer_entero_16(0x3D) / 16384
        az = self.leer_entero_16(0x3F) / 16384

        return ax, ay, az

    def leer_angulos(self):
        """
        Calcula los ángulos frontal y lateral.
        """
        ax, ay, az = self.leer_acelerometro()

        angulo_frontal = math.degrees(
            math.atan2(
                ay,
                math.sqrt((ax * ax) + (az * az))
            )
        )

        angulo_lateral = math.degrees(
            math.atan2(
                -ax,
                math.sqrt((ay * ay) + (az * az))
            )
        )

        return angulo_frontal, angulo_lateral


# =========================================================
# INICIALIZACIÓN DEL HARDWARE
# =========================================================

motores = Pin(
    PIN_MOTORES,
    Pin.OUT,
    value=0
)

i2c = I2C(
    0,
    scl=Pin(PIN_SCL),
    sda=Pin(PIN_SDA),
    freq=400000
)

print("Dispositivos I2C encontrados:", i2c.scan())

try:
    mpu = MPU6050(i2c)
    sensor_disponible = True
    print("MPU6050 conectado correctamente")

except Exception as error:
    mpu = None
    sensor_disponible = False
    print("No se pudo iniciar el MPU6050:", error)


# =========================================================
# VARIABLES DEL SISTEMA
# =========================================================

calibrado = False

angulo_cero_frontal = 0.0
angulo_cero_lateral = 0.0

angulo_frontal_actual = 0.0
angulo_lateral_actual = 0.0

desviacion_frontal = 0.0
desviacion_lateral = 0.0
desviacion_total = 0.0

tolerancia = TOLERANCIA_INICIAL

estado_postura = "Sin calibrar"
nivel_postura = "sin_calibrar"

motores_activos = False
sedentarismo_detectado = False

historial = []

ultimo_estado_registrado = ""
ultimo_registro_historial = 0

ultima_posicion_movimiento = None
ultimo_movimiento = time.ticks_ms()
ultimo_aviso_sedentarismo = time.ticks_ms()

ultima_lectura_sensor = 0


# Variables para controlar vibraciones sin bloquear
tipo_vibracion = "ninguna"
inicio_vibracion = 0
paso_vibracion = 0


# =========================================================
# CONTROL DE MOTORES
# =========================================================

def encender_motores():
    """
    Enciende los dos motores simultáneamente.
    """
    global motores_activos

    motores.value(1)
    motores_activos = True


def apagar_motores():
    """
    Apaga los dos motores simultáneamente.
    """
    global motores_activos

    motores.value(0)
    motores_activos = False


def iniciar_vibracion_sedentarismo():
    """
    Inicia un patrón de dos vibraciones cortas.
    """
    global tipo_vibracion
    global inicio_vibracion
    global paso_vibracion

    tipo_vibracion = "sedentarismo"
    inicio_vibracion = time.ticks_ms()
    paso_vibracion = 0
    encender_motores()


def actualizar_vibracion():
    """
    Actualiza el patrón de vibración sin detener el servidor web.
    """
    global tipo_vibracion
    global inicio_vibracion
    global paso_vibracion

    if tipo_vibracion != "sedentarismo":
        return

    ahora = time.ticks_ms()
    tiempo_transcurrido = time.ticks_diff(
        ahora,
        inicio_vibracion
    )

    # Primera vibración: 300 ms.
    if paso_vibracion == 0 and tiempo_transcurrido >= 300:
        apagar_motores()
        paso_vibracion = 1
        inicio_vibracion = ahora

    # Pausa: 250 ms.
    elif paso_vibracion == 1 and tiempo_transcurrido >= 250:
        encender_motores()
        paso_vibracion = 2
        inicio_vibracion = ahora

    # Segunda vibración: 300 ms.
    elif paso_vibracion == 2 and tiempo_transcurrido >= 300:
        apagar_motores()
        paso_vibracion = 3
        tipo_vibracion = "ninguna"


# =========================================================
# FUNCIONES DEL SENSOR
# =========================================================

def promedio_angulos(cantidad=20):
    """
    Obtiene un promedio de varias lecturas del MPU6050.
    """
    if not sensor_disponible:
        return 0.0, 0.0

    suma_frontal = 0.0
    suma_lateral = 0.0

    for _ in range(cantidad):
        frontal, lateral = mpu.leer_angulos()

        suma_frontal += frontal
        suma_lateral += lateral

        time.sleep_ms(20)

    promedio_frontal = suma_frontal / cantidad
    promedio_lateral = suma_lateral / cantidad

    return promedio_frontal, promedio_lateral


def calibrar_postura():
    """
    Guarda la posición actual como postura correcta.
    """
    global calibrado
    global angulo_cero_frontal
    global angulo_cero_lateral
    global estado_postura
    global nivel_postura
    global ultimo_movimiento
    global ultima_posicion_movimiento
    global sedentarismo_detectado

    if not sensor_disponible:
        return False

    apagar_motores()

    frontal, lateral = promedio_angulos(25)

    angulo_cero_frontal = frontal
    angulo_cero_lateral = lateral

    calibrado = True
    estado_postura = "Postura calibrada"
    nivel_postura = "correcta"

    ultimo_movimiento = time.ticks_ms()
    ultima_posicion_movimiento = (
        frontal,
        lateral
    )

    sedentarismo_detectado = False

    agregar_historial(
        "Calibración",
        0.0,
        0.0,
        "Se guardó una nueva posición cero"
    )

    print("Postura calibrada")
    print("Frontal cero:", angulo_cero_frontal)
    print("Lateral cero:", angulo_cero_lateral)

    return True


def clasificar_postura(desviacion):
    """
    Clasifica la postura dependiendo de la desviación.
    """
    if not calibrado:
        return "Sin calibrar", "sin_calibrar", False

    if desviacion <= tolerancia:
        return "Postura correcta", "correcta", False

    exceso = desviacion - tolerancia

    if exceso <= 5:
        return (
            "Postura ligeramente incorrecta",
            "leve",
            True
        )

    if exceso <= 15:
        return (
            "Mala postura",
            "moderada",
            True
        )

    return (
        "Postura muy mala",
        "grave",
        True
    )


def actualizar_sensor():
    """
    Actualiza las lecturas y el estado de la postura.
    """
    global angulo_frontal_actual
    global angulo_lateral_actual
    global desviacion_frontal
    global desviacion_lateral
    global desviacion_total
    global estado_postura
    global nivel_postura

    if not sensor_disponible:
        estado_postura = "Sensor no encontrado"
        nivel_postura = "error"
        apagar_motores()
        return

    try:
        frontal, lateral = mpu.leer_angulos()

        angulo_frontal_actual = frontal
        angulo_lateral_actual = lateral

        if not calibrado:
            desviacion_frontal = 0.0
            desviacion_lateral = 0.0
            desviacion_total = 0.0

            estado_postura = "Sin calibrar"
            nivel_postura = "sin_calibrar"

            apagar_motores()
            return

        desviacion_frontal = (
            frontal - angulo_cero_frontal
        )

        desviacion_lateral = (
            lateral - angulo_cero_lateral
        )

        desviacion_total = max(
            abs(desviacion_frontal),
            abs(desviacion_lateral)
        )

        estado, nivel, debe_vibrar = clasificar_postura(
            desviacion_total
        )

        estado_postura = estado
        nivel_postura = nivel

        # La vibración de sedentarismo tiene prioridad.
        if tipo_vibracion != "sedentarismo":
            if debe_vibrar:
                encender_motores()
            else:
                apagar_motores()

        registrar_postura()
        revisar_sedentarismo()

    except Exception as error:
        print("Error leyendo el MPU6050:", error)

        estado_postura = "Error de lectura"
        nivel_postura = "error"

        apagar_motores()


# =========================================================
# HISTORIAL TEMPORAL
# =========================================================

def agregar_historial(
    tipo,
    frontal,
    lateral,
    descripcion
):
    """
    Agrega un evento al historial guardado en RAM.
    """
    evento = {
        "tiempo": time.ticks_ms() // 1000,
        "tipo": tipo,
        "frontal": round(frontal, 1),
        "lateral": round(lateral, 1),
        "descripcion": descripcion
    }

    historial.append(evento)

    if len(historial) > MAXIMO_HISTORIAL:
        historial.pop(0)


def registrar_postura():
    """
    Registra cambios importantes de postura.
    """
    global ultimo_estado_registrado
    global ultimo_registro_historial

    ahora = time.ticks_ms()

    cambio_estado = (
        nivel_postura != ultimo_estado_registrado
    )

    han_pasado_15_segundos = (
        time.ticks_diff(
            ahora,
            ultimo_registro_historial
        ) >= 15000
    )

    if cambio_estado or han_pasado_15_segundos:
        agregar_historial(
            "Postura",
            desviacion_frontal,
            desviacion_lateral,
            estado_postura
        )

        ultimo_estado_registrado = nivel_postura
        ultimo_registro_historial = ahora


# =========================================================
# DETECCIÓN DE SEDENTARISMO
# =========================================================

def revisar_sedentarismo():
    """
    Detecta si la persona no ha cambiado de posición.
    """
    global ultima_posicion_movimiento
    global ultimo_movimiento
    global ultimo_aviso_sedentarismo
    global sedentarismo_detectado

    if not calibrado:
        sedentarismo_detectado = False
        return

    ahora = time.ticks_ms()

    posicion_actual = (
        angulo_frontal_actual,
        angulo_lateral_actual
    )

    if ultima_posicion_movimiento is None:
        ultima_posicion_movimiento = posicion_actual
        ultimo_movimiento = ahora
        return

    cambio_frontal = abs(
        posicion_actual[0]
        - ultima_posicion_movimiento[0]
    )

    cambio_lateral = abs(
        posicion_actual[1]
        - ultima_posicion_movimiento[1]
    )

    cambio_total = max(
        cambio_frontal,
        cambio_lateral
    )

    # Si hubo movimiento suficiente, reinicia el tiempo.
    if cambio_total >= UMBRAL_MOVIMIENTO:
        ultima_posicion_movimiento = posicion_actual
        ultimo_movimiento = ahora
        sedentarismo_detectado = False
        return

    tiempo_sin_movimiento = time.ticks_diff(
        ahora,
        ultimo_movimiento
    ) // 1000

    tiempo_desde_ultimo_aviso = time.ticks_diff(
        ahora,
        ultimo_aviso_sedentarismo
    ) // 1000

    if (
        tiempo_sin_movimiento >= TIEMPO_SEDENTARISMO
        and
        tiempo_desde_ultimo_aviso >= TIEMPO_ENTRE_AVISOS
        and
        tipo_vibracion == "ninguna"
    ):
        sedentarismo_detectado = True
        ultimo_aviso_sedentarismo = ahora

        agregar_historial(
            "Sedentarismo",
            desviacion_frontal,
            desviacion_lateral,
            "Mucho tiempo sin cambiar de posición"
        )

        iniciar_vibracion_sedentarismo()


def obtener_tiempo_sin_movimiento():
    """
    Retorna los segundos sin cambios importantes de posición.
    """
    tiempo = time.ticks_diff(
        time.ticks_ms(),
        ultimo_movimiento
    ) // 1000

    return max(0, tiempo)


# =========================================================
# DATOS JSON
# =========================================================

def obtener_estado_json():
    """
    Retorna el estado del sistema en formato JSON.
    """
    datos = {
        "sensor": sensor_disponible,
        "calibrado": calibrado,

        "angulo_frontal": round(
            angulo_frontal_actual,
            1
        ),

        "angulo_lateral": round(
            angulo_lateral_actual,
            1
        ),

        "desviacion_frontal": round(
            desviacion_frontal,
            1
        ),

        "desviacion_lateral": round(
            desviacion_lateral,
            1
        ),

        "desviacion_total": round(
            desviacion_total,
            1
        ),

        "tolerancia": round(
            tolerancia,
            1
        ),

        "estado": estado_postura,
        "nivel": nivel_postura,

        "motores": motores_activos,

        "sedentarismo": sedentarismo_detectado,

        "sin_movimiento": obtener_tiempo_sin_movimiento()
    }

    return ujson.dumps(datos)


def obtener_historial_json():
    """
    Retorna el historial completo en formato JSON.
    """
    return ujson.dumps(historial)


# =========================================================
# PÁGINA WEB
# =========================================================

PAGINA_WEB = """<!DOCTYPE html>
<html lang="es">

<head>
    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >

    <title>Mi corrector de postura</title>

    <style>
        :root {
            --morado-oscuro: #4c1d78;
            --morado: #8b5cf6;
            --morado-suave: #c4b5fd;
            --rosa: #ec4899;
            --rosa-suave: #fbcfe8;
            --crema: #fdf4ff;
            --texto: #3b2557;
            --texto-suave: #7c6a91;
            --blanco: #ffffff;

            --verde: #22c55e;
            --amarillo: #f0b429;
            --naranja: #f2994a;
            --rojo: #ef4565;
            --gris: #9891a8;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            min-height: 100vh;
            background:
                radial-gradient(
                    circle at 10% -10%,
                    #f3e8ff 0%,
                    transparent 45%
                ),
                radial-gradient(
                    circle at 100% 0%,
                    #ffe4f2 0%,
                    transparent 40%
                ),
                var(--crema);
            color: var(--texto);
            font-family:
                'Poppins', 'Trebuchet MS', 'Segoe UI',
                system-ui, sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        header {
            padding: 34px 20px 46px;
            background: linear-gradient(
                120deg,
                var(--morado-oscuro),
                var(--morado) 55%,
                var(--rosa)
            );
            color: white;
            text-align: center;
            border-radius: 0 0 32px 32px;
            box-shadow: 0 10px 30px rgba(140, 60, 180, 0.25);
        }

        header .icono {
            font-size: 34px;
            display: block;
            margin-bottom: 4px;
        }

        header h1 {
            margin: 0 0 6px;
            font-size: 26px;
            font-weight: 700;
            letter-spacing: 0.2px;
        }

        header p {
            margin: 0;
            opacity: 0.9;
            font-size: 14px;
        }

        main {
            width: min(980px, 94%);
            margin: -26px auto 30px;
        }

        /* -------- Orbe de estado -------- */

        .orbe-tarjeta {
            background: var(--blanco);
            border-radius: 26px;
            padding: 26px 22px;
            box-shadow: 0 10px 30px rgba(140, 60, 180, 0.12);
            display: flex;
            align-items: center;
            gap: 22px;
            margin-bottom: 16px;
        }

        .orbe {
            flex: 0 0 auto;
            width: 92px;
            height: 92px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            background: linear-gradient(
                145deg,
                var(--morado-suave),
                var(--rosa-suave)
            );
            box-shadow:
                inset 0 0 0 4px rgba(255, 255, 255, 0.6);
            transition: background 0.4s ease;
            animation: respirar 3.4s ease-in-out infinite;
        }

        @keyframes respirar {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); }
        }

        .orbe.correcta {
            background: linear-gradient(145deg, #86efac, #22c55e);
        }

        .orbe.leve {
            background: linear-gradient(145deg, #fde68a, #f0b429);
        }

        .orbe.moderada {
            background: linear-gradient(145deg, #fdba74, #f2994a);
        }

        .orbe.grave {
            background: linear-gradient(145deg, #fca5b1, #ef4565);
        }

        .orbe.error,
        .orbe.sin_calibrar {
            background: linear-gradient(145deg, #e2ddef, var(--gris));
        }

        .orbe-info h2 {
            margin: 0 0 4px;
            font-size: 20px;
        }

        .orbe-info p {
            margin: 0;
            color: var(--texto-suave);
            font-size: 14.5px;
        }

        /* -------- Recordatorios amigables -------- */

        .recordatorio {
            display: none;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
            padding: 16px 18px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 14.5px;
            color: var(--texto);
            background: linear-gradient(
                90deg,
                var(--rosa-suave),
                var(--morado-suave)
            );
            box-shadow: 0 6px 18px rgba(140, 60, 180, 0.15);
        }

        .recordatorio .emoji {
            font-size: 24px;
        }

        .recordatorio.sedentarismo {
            background: linear-gradient(90deg, #fde9c8, #fbcfe8);
        }

        /* -------- Tarjetas de métricas -------- */

        .grid {
            display: grid;
            grid-template-columns:
                repeat(auto-fit, minmax(210px, 1fr));
            gap: 14px;
            margin-bottom: 16px;
        }

        .card {
            padding: 20px;
            background: var(--blanco);
            border-radius: 22px;
            box-shadow: 0 8px 22px rgba(140, 60, 180, 0.10);
            border: 1px solid rgba(196, 181, 253, 0.35);
        }

        .card h2 {
            margin: 0 0 10px;
            font-size: 14.5px;
            color: var(--texto-suave);
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .value {
            margin: 4px 0;
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(
                90deg,
                var(--morado),
                var(--rosa)
            );
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }

        .subtexto {
            font-size: 13px;
            color: var(--texto-suave);
        }

        /* -------- Panel de configuración -------- */

        .config-fila {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 6px;
        }

        button {
            border: none;
            border-radius: 999px;
            padding: 12px 20px;
            font-size: 14px;
            font-weight: 600;
            color: white;
            cursor: pointer;
            background: linear-gradient(
                100deg,
                var(--morado),
                var(--rosa)
            );
            box-shadow: 0 6px 16px rgba(140, 60, 180, 0.30);
            transition: transform 0.15s ease, opacity 0.15s ease;
        }

        button:hover {
            transform: translateY(-2px);
            opacity: 0.94;
        }

        button:active {
            transform: translateY(0);
        }

        button.secundario {
            background: var(--blanco);
            color: var(--morado);
            box-shadow: inset 0 0 0 2px var(--morado-suave);
        }

        button.peligro {
            background: linear-gradient(100deg, #f2994a, #ef4565);
        }

        input[type="number"] {
            width: 90px;
            padding: 10px 12px;
            border: 2px solid var(--morado-suave);
            border-radius: 12px;
            font-size: 15px;
            color: var(--texto);
            outline: none;
        }

        input[type="number"]:focus {
            border-color: var(--morado);
        }

        label {
            font-size: 14px;
            color: var(--texto-suave);
            align-self: center;
        }

        hr {
            border: none;
            border-top: 1px dashed var(--rosa-suave);
            margin: 16px 0;
        }

        /* -------- Gráfica de progreso -------- */

        .grafica-tarjeta {
            padding: 22px;
            background: var(--blanco);
            border-radius: 22px;
            box-shadow: 0 8px 22px rgba(140, 60, 180, 0.10);
            margin-bottom: 16px;
        }

        .grafica-encabezado {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 10px;
        }

        .grafica-encabezado h2 {
            margin: 0;
            font-size: 16px;
        }

        .leyenda {
            display: flex;
            gap: 14px;
            font-size: 12.5px;
            color: var(--texto-suave);
        }

        .leyenda span {
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }

        .punto-leyenda {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            display: inline-block;
        }

        canvas#grafica {
            width: 100%;
            height: 190px;
            display: block;
        }

        /* -------- Historial -------- */

        .historial-tarjeta {
            padding: 22px;
            background: var(--blanco);
            border-radius: 22px;
            box-shadow: 0 8px 22px rgba(140, 60, 180, 0.10);
            overflow-x: auto;
        }

        .historial-tarjeta h2 {
            margin: 0 0 12px;
            font-size: 16px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 10px 8px;
            text-align: left;
            font-size: 13.5px;
        }

        th {
            color: var(--texto-suave);
            font-weight: 600;
            border-bottom: 2px solid var(--rosa-suave);
        }

        tbody tr:nth-child(odd) {
            background: #faf5ff;
        }

        tbody tr {
            border-radius: 10px;
        }

        .etiqueta {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            color: white;
            background: var(--morado);
        }

        .etiqueta.Sedentarismo {
            background: var(--naranja);
        }

        .etiqueta.Calibración {
            background: var(--verde);
        }

        footer {
            text-align: center;
            color: var(--texto-suave);
            font-size: 12.5px;
            margin: 20px 0 30px;
        }

        @media (max-width: 600px) {
            .orbe-tarjeta {
                flex-direction: column;
                text-align: center;
            }

            .value {
                font-size: 24px;
            }

            th, td {
                font-size: 12px;
            }
        }
    </style>
</head>

<body>

<header>
    <span class="icono">🌸</span>
    <h1>Mi corrector de postura</h1>
    <p>Tu compañero suave para cuidar la espalda, momento a momento</p>
</header>

<main>

    <div id="recordatorioPostura" class="recordatorio">
        <span class="emoji">💜</span>
        <span id="textoRecordatorioPostura">
            Notamos que te encorvaste un poquito, ¿enderezamos la espalda?
        </span>
    </div>

    <div id="recordatorioSedentarismo" class="recordatorio sedentarismo">
        <span class="emoji">🌷</span>
        <span>
            Llevas un buen rato sin moverte. Levántate, estira los brazos
            y respira hondo un momento.
        </span>
    </div>

    <section class="orbe-tarjeta">
        <div id="orbe" class="orbe sin_calibrar">🙂</div>

        <div class="orbe-info">
            <h2 id="estado">Sin calibrar todavía</h2>
            <p id="mensajeGravedad">
                Guarda tu posición correcta para empezar a acompañarte.
            </p>
        </div>
    </section>

    <section class="grid">

        <article class="card">
            <h2>📐 Desviación máxima</h2>
            <div class="value"><span id="desviacionTotal">0.0</span>°</div>
            <div class="subtexto">Comparada con tu postura guardada</div>
        </article>

        <article class="card">
            <h2>⬆️ Ángulo frontal</h2>
            <div class="value"><span id="frontal">0.0</span>°</div>
            <div class="subtexto">
                Desviación: <span id="desviacionFrontal">0.0</span>°
            </div>
        </article>

        <article class="card">
            <h2>↔️ Ángulo lateral</h2>
            <div class="value"><span id="lateral">0.0</span>°</div>
            <div class="subtexto">
                Desviación: <span id="desviacionLateral">0.0</span>°
            </div>
        </article>

        <article class="card">
            <h2>🕰️ Sin moverte</h2>
            <div class="value"><span id="sinMovimiento">0</span> s</div>
            <div class="subtexto">
                Motores: <span id="motores">Apagados</span>
            </div>
        </article>

        <article class="card">
            <h2>🔌 Estado del equipo</h2>
            <div class="subtexto">
                Sensor:
                <strong id="sensor">Desconocido</strong>
            </div>
            <div class="subtexto">
                Calibración:
                <strong id="calibrado">No</strong>
            </div>
        </article>

    </section>

    <section class="grafica-tarjeta">
        <div class="grafica-encabezado">
            <h2>📈 Progreso de tu postura</h2>

            <div class="leyenda">
                <span>
                    <span
                        class="punto-leyenda"
                        style="background:#8b5cf6"
                    ></span>
                    Desviación
                </span>
                <span>
                    <span
                        class="punto-leyenda"
                        style="background:#ec4899"
                    ></span>
                    Tu tolerancia
                </span>
            </div>
        </div>

        <canvas id="grafica"></canvas>

        <div
            id="mensajeGrafica"
            class="subtexto"
            style="margin-top:8px;"
        >
            Calibra tu postura y muévete un poco para empezar a ver tu
            progreso aquí.
        </div>
    </section>

    <section class="card">
        <h2 style="margin-top:0;">⚙️ Configuración</h2>

        <div class="config-fila">
            <button onclick="calibrar()">
                💾 Guardar mi posición correcta
            </button>

            <button class="peligro" onclick="detenerMotores()">
                ✋ Detener motores
            </button>
        </div>

        <hr>

        <div class="config-fila">
            <label for="tolerancia">Tolerancia:</label>

            <input
                id="tolerancia"
                type="number"
                min="3"
                max="45"
                step="1"
                value="12"
            >

            <span class="subtexto">grados</span>

            <button onclick="guardarTolerancia()">
                Guardar tolerancia
            </button>

            <button class="secundario" onclick="borrarHistorial()">
                🗑️ Borrar historial
            </button>
        </div>
    </section>

    <section class="historial-tarjeta">
        <h2>📝 Historial temporal</h2>

        <table>
            <thead>
                <tr>
                    <th>Tiempo</th>
                    <th>Tipo</th>
                    <th>Frontal</th>
                    <th>Lateral</th>
                    <th>Descripción</th>
                </tr>
            </thead>

            <tbody id="historial">
                <tr>
                    <td colspan="5">Todavía no hay registros. 🌱</td>
                </tr>
            </tbody>
        </table>
    </section>

</main>

<footer>
    Hecho con cariño para cuidar tu espalda 💜🌸
</footer>

<script>
    let primeraCarga = true;
    let toleranciaActual = 12;

    const emojiPorNivel = {
        correcta: "😊",
        leve: "🙂",
        moderada: "😕",
        grave: "😣",
        error: "😴",
        sin_calibrar: "🙂"
    };

    async function cargarEstado() {
        try {
            const respuesta = await fetch("/status");
            const datos = await respuesta.json();

            toleranciaActual = datos.tolerancia;

            document.getElementById("estado").textContent =
                datos.estado;

            document.getElementById("frontal").textContent =
                datos.angulo_frontal.toFixed(1);

            document.getElementById("lateral").textContent =
                datos.angulo_lateral.toFixed(1);

            document.getElementById(
                "desviacionFrontal"
            ).textContent = datos.desviacion_frontal.toFixed(1);

            document.getElementById(
                "desviacionLateral"
            ).textContent = datos.desviacion_lateral.toFixed(1);

            document.getElementById(
                "desviacionTotal"
            ).textContent = datos.desviacion_total.toFixed(1);

            document.getElementById("motores").textContent =
                datos.motores ? "Vibrando" : "Apagados";

            document.getElementById("calibrado").textContent =
                datos.calibrado ? "Sí" : "No";

            document.getElementById("sensor").textContent =
                datos.sensor ? "Conectado" : "No encontrado";

            document.getElementById(
                "sinMovimiento"
            ).textContent = datos.sin_movimiento;

            const orbe = document.getElementById("orbe");
            orbe.className = "orbe " + datos.nivel;
            orbe.textContent =
                emojiPorNivel[datos.nivel] || "🙂";

            const posturaIncorrecta = [
                "leve", "moderada", "grave"
            ].includes(datos.nivel);

            const avisoPostura = document.getElementById(
                "recordatorioPostura"
            );

            avisoPostura.style.display =
                posturaIncorrecta ? "flex" : "none";

            const textos = {
                leve: "Notamos que te encorvaste un poquito, " +
                    "¿enderezamos la espalda?",
                moderada: "Tu espalda pide un ajuste. Respira, " +
                    "endereza los hombros y vuelve al centro.",
                grave: "Postura muy forzada. Hagamos una pausa " +
                    "y corrijamos la posición con calma."
            };

            if (textos[datos.nivel]) {
                document.getElementById(
                    "textoRecordatorioPostura"
                ).textContent = textos[datos.nivel];
            }

            document.getElementById(
                "recordatorioSedentarismo"
            ).style.display = datos.sedentarismo ? "flex" : "none";

            const mensajes = {
                correcta: "¡Vas muy bien! Tu postura está dentro " +
                    "de lo esperado. 🌸",
                leve: "Una desviación leve, nada que un pequeño " +
                    "ajuste no resuelva.",
                moderada: "Tu postura se alejó bastante de lo " +
                    "ideal. Vale la pena corregirla ahora.",
                grave: "Postura muy forzada por ahora, mejor " +
                    "corregirla cuanto antes.",
                error: "No logramos leer el sensor. Revisa la " +
                    "conexión del MPU6050.",
                sin_calibrar: "Guarda tu posición correcta para " +
                    "empezar a acompañarte."
            };

            document.getElementById("mensajeGravedad").textContent =
                mensajes[datos.nivel] || "";

            if (primeraCarga) {
                document.getElementById("tolerancia").value =
                    datos.tolerancia;

                primeraCarga = false;
            }

        } catch (error) {
            document.getElementById("estado").textContent =
                "Sin conexión";
        }
    }

    async function calibrar() {
        try {
            const respuesta = await fetch("/calibrate");
            const resultado = await respuesta.json();

            alert(resultado.mensaje);

            await cargarEstado();
            await cargarHistorial();

        } catch (error) {
            alert("No fue posible calibrar.");
        }
    }

    async function guardarTolerancia() {
        const valor =
            document.getElementById("tolerancia").value;

        try {
            const respuesta = await fetch(
                "/tolerance?value=" + encodeURIComponent(valor)
            );

            const resultado = await respuesta.json();

            alert(resultado.mensaje);

            await cargarEstado();

        } catch (error) {
            alert("No fue posible guardar la tolerancia.");
        }
    }

    async function detenerMotores() {
        try {
            await fetch("/motors-off");
            await cargarEstado();

        } catch (error) {
            alert("No fue posible detener los motores.");
        }
    }

    async function borrarHistorial() {
        try {
            const respuesta = await fetch("/clear-history");
            const resultado = await respuesta.json();

            alert(resultado.mensaje);

            await cargarHistorial();

        } catch (error) {
            alert("No fue posible borrar el historial.");
        }
    }

    function colorPorDesviacion(desviacion) {
        if (desviacion <= toleranciaActual) return "#22c55e";

        const exceso = desviacion - toleranciaActual;

        if (exceso <= 5) return "#f0b429";
        if (exceso <= 15) return "#f2994a";

        return "#ef4565";
    }

    function dibujarGrafica(eventos) {
        const canvas = document.getElementById("grafica");
        const mensaje = document.getElementById("mensajeGrafica");

        const puntos = eventos
            .filter(e => e.tipo === "Postura")
            .slice(-20)
            .map(e => ({
                tiempo: e.tiempo,
                desviacion: Math.max(
                    Math.abs(e.frontal),
                    Math.abs(e.lateral)
                )
            }));

        const ancho = canvas.clientWidth || 600;
        const alto = 190;

        canvas.width = ancho * devicePixelRatio;
        canvas.height = alto * devicePixelRatio;

        const ctx = canvas.getContext("2d");
        ctx.scale(devicePixelRatio, devicePixelRatio);
        ctx.clearRect(0, 0, ancho, alto);

        if (puntos.length < 2) {
            mensaje.style.display = "block";
            return;
        }

        mensaje.style.display = "none";

        const margen = { arriba: 14, abajo: 24, izq: 34, der: 12 };
        const areaAncho = ancho - margen.izq - margen.der;
        const areaAlto = alto - margen.arriba - margen.abajo;

        const maxDato = Math.max(
            ...puntos.map(p => p.desviacion),
            toleranciaActual
        ) * 1.15;

        const minTiempo = puntos[0].tiempo;
        const maxTiempo = puntos[puntos.length - 1].tiempo;
        const rangoTiempo = Math.max(1, maxTiempo - minTiempo);

        function x(t) {
            return margen.izq +
                ((t - minTiempo) / rangoTiempo) * areaAncho;
        }

        function y(valor) {
            return margen.arriba +
                areaAlto - (valor / maxDato) * areaAlto;
        }

        // Líneas guía horizontales suaves.
        ctx.strokeStyle = "#f1e9fb";
        ctx.lineWidth = 1;

        for (let i = 0; i <= 3; i++) {
            const yy = margen.arriba + (areaAlto / 3) * i;

            ctx.beginPath();
            ctx.moveTo(margen.izq, yy);
            ctx.lineTo(ancho - margen.der, yy);
            ctx.stroke();
        }

        // Línea punteada de tolerancia (la meta).
        ctx.strokeStyle = "#ec4899";
        ctx.setLineDash([5, 5]);
        ctx.lineWidth = 1.5;

        ctx.beginPath();
        ctx.moveTo(margen.izq, y(toleranciaActual));
        ctx.lineTo(ancho - margen.der, y(toleranciaActual));
        ctx.stroke();

        ctx.setLineDash([]);

        // Área bajo la curva con degradado morado-rosa.
        const degradado = ctx.createLinearGradient(
            0, margen.arriba, 0, alto - margen.abajo
        );

        degradado.addColorStop(0, "rgba(139, 92, 246, 0.35)");
        degradado.addColorStop(1, "rgba(236, 72, 153, 0.02)");

        ctx.beginPath();
        ctx.moveTo(x(puntos[0].tiempo), y(puntos[0].desviacion));

        puntos.forEach(p => {
            ctx.lineTo(x(p.tiempo), y(p.desviacion));
        });

        ctx.lineTo(x(puntos[puntos.length - 1].tiempo), alto - margen.abajo);
        ctx.lineTo(x(puntos[0].tiempo), alto - margen.abajo);
        ctx.closePath();
        ctx.fillStyle = degradado;
        ctx.fill();

        // Línea principal.
        ctx.beginPath();
        ctx.moveTo(x(puntos[0].tiempo), y(puntos[0].desviacion));

        puntos.forEach(p => {
            ctx.lineTo(x(p.tiempo), y(p.desviacion));
        });

        ctx.strokeStyle = "#8b5cf6";
        ctx.lineWidth = 2.5;
        ctx.lineJoin = "round";
        ctx.stroke();

        // Puntos coloreados según severidad.
        puntos.forEach(p => {
            ctx.beginPath();
            ctx.arc(x(p.tiempo), y(p.desviacion), 4, 0, Math.PI * 2);
            ctx.fillStyle = colorPorDesviacion(p.desviacion);
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = "#ffffff";
            ctx.stroke();
        });

        // Etiquetas de eje Y.
        ctx.fillStyle = "#7c6a91";
        ctx.font = "11px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(
            maxDato.toFixed(0) + "°",
            margen.izq - 6,
            margen.arriba + 4
        );
        ctx.fillText("0°", margen.izq - 6, alto - margen.abajo);
    }

    async function cargarHistorial() {
        try {
            const respuesta = await fetch("/history");
            const eventos = await respuesta.json();

            dibujarGrafica(eventos);

            const cuerpo = document.getElementById("historial");

            if (eventos.length === 0) {
                cuerpo.innerHTML =
                    '<tr><td colspan="5">' +
                    'Todavía no hay registros. 🌱</td></tr>';

                return;
            }

            cuerpo.innerHTML = "";

            eventos
                .slice()
                .reverse()
                .forEach(evento => {
                    const fila = document.createElement("tr");

                    const tipoSeguro = document.createElement("span");
                    tipoSeguro.className = "etiqueta " + evento.tipo;
                    tipoSeguro.textContent = evento.tipo;

                    const celdaTiempo = document.createElement("td");
                    celdaTiempo.textContent = evento.tiempo + " s";

                    const celdaTipo = document.createElement("td");
                    celdaTipo.appendChild(tipoSeguro);

                    const celdaFrontal = document.createElement("td");
                    celdaFrontal.textContent = evento.frontal + "°";

                    const celdaLateral = document.createElement("td");
                    celdaLateral.textContent = evento.lateral + "°";

                    const celdaDescripcion = document.createElement("td");
                    celdaDescripcion.textContent = evento.descripcion;

                    fila.append(
                        celdaTiempo,
                        celdaTipo,
                        celdaFrontal,
                        celdaLateral,
                        celdaDescripcion
                    );

                    cuerpo.appendChild(fila);
                });

        } catch (error) {
            console.log("Error cargando historial");
        }
    }

    cargarEstado();
    cargarHistorial();

    setInterval(cargarEstado, 1000);
    setInterval(cargarHistorial, 5000);

    window.addEventListener("resize", cargarHistorial);
</script>

</body>
</html>
"""


# =========================================================
# SERVIDOR WEB
# =========================================================

def enviar_respuesta(
    conexion,
    contenido,
    tipo="text/html",
    codigo="200 OK"
):
    """
    Envía una respuesta HTTP.
    """
    encabezado = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: {}; charset=utf-8\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n"
        "\r\n"
    ).format(codigo, tipo)

    conexion.send(encabezado.encode())
    conexion.send(contenido.encode())


def obtener_ruta(solicitud):
    """
    Obtiene la ruta solicitada por el navegador.
    """
    try:
        primera_linea = solicitud.split("\r\n")[0]
        ruta = primera_linea.split(" ")[1]

        return ruta

    except Exception:
        return "/"


def obtener_parametro(ruta, nombre):
    """
    Obtiene un parámetro de la URL.
    """
    if "?" not in ruta:
        return None

    consulta = ruta.split("?", 1)[1]

    for elemento in consulta.split("&"):
        if "=" in elemento:
            clave, valor = elemento.split("=", 1)

            if clave == nombre:
                return valor

    return None


def procesar_cliente(conexion):
    """
    Procesa una solicitud HTTP.
    """
    global tolerancia
    global historial
    global tipo_vibracion

    try:
        conexion.settimeout(2)

        solicitud = conexion.recv(2048).decode()
        ruta = obtener_ruta(solicitud)

        if ruta == "/":
            enviar_respuesta(
                conexion,
                PAGINA_WEB,
                "text/html"
            )

        elif ruta.startswith("/status"):
            enviar_respuesta(
                conexion,
                obtener_estado_json(),
                "application/json"
            )

        elif ruta.startswith("/history"):
            enviar_respuesta(
                conexion,
                obtener_historial_json(),
                "application/json"
            )

        elif ruta.startswith("/calibrate"):
            resultado = calibrar_postura()

            if resultado:
                mensaje = {
                    "ok": True,
                    "mensaje": (
                        "La posición actual fue guardada "
                        "como postura correcta."
                    )
                }

            else:
                mensaje = {
                    "ok": False,
                    "mensaje": (
                        "No se encontró el MPU6050."
                    )
                }

            enviar_respuesta(
                conexion,
                ujson.dumps(mensaje),
                "application/json"
            )

        elif ruta.startswith("/tolerance"):
            valor = obtener_parametro(
                ruta,
                "value"
            )

            try:
                nueva_tolerancia = float(valor)

                if nueva_tolerancia < 3:
                    nueva_tolerancia = 3

                if nueva_tolerancia > 45:
                    nueva_tolerancia = 45

                tolerancia = nueva_tolerancia

                mensaje = {
                    "ok": True,
                    "mensaje": (
                        "Tolerancia guardada: "
                        + str(tolerancia)
                        + " grados."
                    )
                }

            except Exception:
                mensaje = {
                    "ok": False,
                    "mensaje": (
                        "La tolerancia ingresada "
                        "no es válida."
                    )
                }

            enviar_respuesta(
                conexion,
                ujson.dumps(mensaje),
                "application/json"
            )

        elif ruta.startswith("/motors-off"):
            tipo_vibracion = "ninguna"
            apagar_motores()

            mensaje = {
                "ok": True,
                "mensaje": "Motores detenidos."
            }

            enviar_respuesta(
                conexion,
                ujson.dumps(mensaje),
                "application/json"
            )

        elif ruta.startswith("/clear-history"):
            historial.clear()

            mensaje = {
                "ok": True,
                "mensaje": "Historial borrado."
            }

            enviar_respuesta(
                conexion,
                ujson.dumps(mensaje),
                "application/json"
            )

        else:
            enviar_respuesta(
                conexion,
                "Ruta no encontrada",
                "text/plain",
                "404 Not Found"
            )

    except Exception as error:
        print("Error atendiendo cliente:", error)

    finally:
        conexion.close()


# =========================================================
# PUNTO DE ACCESO WIFI
# =========================================================

def iniciar_wifi():
    """
    Inicia la ESP32-S3 como punto de acceso.
    """
    wifi = network.WLAN(network.AP_IF)

    wifi.active(False)
    time.sleep_ms(200)

    wifi.active(True)

    wifi.config(
        essid=NOMBRE_WIFI,
        password=CLAVE_WIFI,
        authmode=network.AUTH_WPA_WPA2_PSK
    )

    while not wifi.active():
        time.sleep_ms(100)

    ip = wifi.ifconfig()[0]

    print()
    print("=================================")
    print("Red Wi-Fi iniciada")
    print("Nombre:", NOMBRE_WIFI)
    print("Contraseña:", CLAVE_WIFI)
    print("Dirección web: http://" + ip)
    print("=================================")
    print()

    return wifi


# =========================================================
# PROGRAMA PRINCIPAL
# =========================================================

wifi = iniciar_wifi()

servidor = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

servidor.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

servidor.bind(
    ("0.0.0.0", 80)
)

servidor.listen(3)
servidor.settimeout(0.1)

print("Servidor web iniciado")
print("Abre http://192.168.4.1")


while True:
    ahora = time.ticks_ms()

    # Leer el MPU6050 cada 100 milisegundos.
    if time.ticks_diff(
        ahora,
        ultima_lectura_sensor
    ) >= 100:

        actualizar_sensor()
        ultima_lectura_sensor = ahora

    # Actualizar patrones de vibración.
    actualizar_vibracion()

    try:
        cliente, direccion = servidor.accept()
        procesar_cliente(cliente)

    except OSError:
        # Es normal cuando no hay clientes nuevos.
        pass

    except Exception as error:
        print("Error del servidor:", error)
        apagar_motores()

    time.sleep_ms(5)